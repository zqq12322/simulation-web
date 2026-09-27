"""
认证与用户隔离测试。

这是本项目第一次处理"别人的数据"。因此断言分四组，每一组都对应一类
**写错了也不会报错**的缺陷：

1. `PasswordHashingTest`——口令哈希：加盐、常数时间比较、参数可升级、
   畸形哈希不能让服务端崩或分配巨量内存；
2. `AuthStoreTest`——会话：令牌只以**哈希**入库、可吊销、过期即失效；
3. `AuthApiTest`——HTTP 语义：401/400/422、用户名归一化、
   以及**不区分"用户不存在"与"口令错误"**（否则等于免费提供用户名枚举）；
4. `ProjectOwnershipTest`——隔离：别人的项目看不到、改不了、删不了，
   且**用 404 而不是 403**（不泄露某个 id 是否存在）。
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import auth as auth_module
import project_store as project_store_module
import projects as projects_module
from auth_store import AuthStore
from passwords import hash_password, needs_rehash, verify_password
from project_store import ProjectStore


class PasswordHashingTest(unittest.TestCase):
    def test_hash_format_is_self_describing(self):
        """参数写在哈希串里，否则将来调参数会让所有老用户登不上。"""
        stored = hash_password("correct horse battery")
        algorithm, n, r, p, salt, digest = stored.split("$")
        self.assertEqual(algorithm, "scrypt")
        self.assertEqual((int(n), int(r), int(p)), (16384, 8, 1))
        self.assertEqual(len(bytes.fromhex(salt)), 16)
        self.assertEqual(len(bytes.fromhex(digest)), 32)

    def test_same_password_hashes_differently(self):
        """每个用户独立随机盐——共用盐会让彩虹表重新变得可行。"""
        first = hash_password("same-password")
        second = hash_password("same-password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("same-password", first))
        self.assertTrue(verify_password("same-password", second))

    def test_verify_rejects_wrong_password(self):
        stored = hash_password("hunter22!")
        self.assertFalse(verify_password("hunter22", stored))
        self.assertFalse(verify_password("Hunter22!", stored))
        self.assertFalse(verify_password("", stored))

    def test_verify_never_raises_on_malformed_hash(self):
        """
        数据库被写坏时也必须只是"验证失败"，不能把异常抛到认证端点外面
        （那会变成 500，还可能泄露内部信息）。
        """
        for broken in [
            "", "not-a-hash", "scrypt$16384$8", "scrypt$abc$8$1$00$00",
            "bcrypt$16384$8$1$00" + "00" * 15 + "$" + "00" * 32,
            "scrypt$16384$8$1$zz$zz",  # salt/hash 不是十六进制
        ]:
            with self.subTest(stored=broken):
                self.assertFalse(verify_password("whatever", broken))

    def test_absurd_parameters_are_rejected_before_hashing(self):
        """
        参数来自数据库。若原样交给 scrypt，一个畸形串就能让进程分配巨量内存。
        """
        huge_n = "scrypt$1073741824$8$1$" + "00" * 16 + "$" + "00" * 32
        self.assertFalse(verify_password("x", huge_n))

        huge_r = "scrypt$16384$1024$1$" + "00" * 16 + "$" + "00" * 32
        self.assertFalse(verify_password("x", huge_r))

        not_power_of_two = "scrypt$10000$8$1$" + "00" * 16 + "$" + "00" * 32
        self.assertFalse(verify_password("x", not_power_of_two))

    def test_empty_password_is_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("")

    def test_needs_rehash_detects_old_parameters(self):
        self.assertFalse(needs_rehash(hash_password("x")))
        old = "scrypt$1024$8$1$" + "00" * 16 + "$" + "00" * 32
        self.assertTrue(needs_rehash(old))
        # 无法解析的串也需要重算
        self.assertTrue(needs_rehash("garbage"))


class AuthStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "auth.db"
        self.store = AuthStore(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_and_fetch_user(self):
        user = self.store.create_user("alice", "hunter22!", display_name="Alice")
        self.assertEqual(user["username"], "alice")
        self.assertEqual(user["displayName"], "Alice")
        self.assertEqual(self.store.count_users(), 1)
        self.assertEqual(self.store.get_user(user["id"])["id"], user["id"])

    def test_duplicate_username_is_rejected(self):
        self.store.create_user("alice", "hunter22!")
        with self.assertRaises(ValueError):
            self.store.create_user("alice", "another-pass")

    def test_authenticate(self):
        self.store.create_user("alice", "hunter22!")
        self.assertIsNotNone(self.store.authenticate("alice", "hunter22!"))
        self.assertIsNone(self.store.authenticate("alice", "wrong"))
        self.assertIsNone(self.store.authenticate("nobody", "hunter22!"))

    def test_users_persist_across_store_instances(self):
        user = self.store.create_user("alice", "hunter22!")
        reopened = AuthStore(self.db_path)
        self.assertEqual(reopened.count_users(), 1)
        self.assertIsNotNone(reopened.authenticate("alice", "hunter22!"))
        self.assertEqual(reopened.get_user(user["id"])["username"], "alice")

    def test_password_hash_is_never_the_plaintext(self):
        user = self.store.create_user("alice", "hunter22!")
        self.assertNotIn("hunter22!", user["passwordHash"])
        self.assertTrue(user["passwordHash"].startswith("scrypt$"))

    # ------------------------------------------------------------- 会话
    def test_token_round_trip(self):
        user = self.store.create_user("alice", "hunter22!")
        issued = self.store.issue_token(user["id"])
        self.assertTrue(issued["token"])
        resolved = self.store.resolve_token(issued["token"])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["id"], user["id"])

    def test_database_does_not_contain_the_raw_token(self):
        """
        关键安全断言：库里只存令牌的**哈希**。

        数据库文件被读走时，攻击者拿不到可以直接使用的登录令牌。
        这条如果写错，整个"令牌只出现一次"的设计就白做了。
        """
        user = self.store.create_user("alice", "hunter22!")
        issued = self.store.issue_token(user["id"])

        connection = sqlite3.connect(str(self.db_path))
        try:
            rows = connection.execute(
                "SELECT token_hash FROM sessions"
            ).fetchall()
        finally:
            connection.close()

        stored_values = [row[0] for row in rows]
        self.assertEqual(len(stored_values), 1)
        self.assertNotEqual(stored_values[0], issued["token"])
        self.assertNotIn(issued["token"], stored_values[0])
        # 但它确实能被解析回来（存的是它的 SHA-256）
        self.assertEqual(self.store.resolve_token(issued["token"])["id"], user["id"])

    def test_unknown_token_resolves_to_none(self):
        self.assertIsNone(self.store.resolve_token("made-up-token"))
        self.assertIsNone(self.store.resolve_token(""))

    def test_revoke_token(self):
        user = self.store.create_user("alice", "hunter22!")
        issued = self.store.issue_token(user["id"])
        self.assertTrue(self.store.revoke_token(issued["token"]))
        self.assertIsNone(self.store.resolve_token(issued["token"]))
        # 幂等：再删一次返回 False，但不报错
        self.assertFalse(self.store.revoke_token(issued["token"]))

    def test_expired_token_is_rejected(self):
        """用负的 TTL 造一个过期会话，不必真的等。"""
        user = self.store.create_user("alice", "hunter22!")
        issued = self.store.issue_token(user["id"], ttl_days=-1)
        self.assertIsNone(self.store.resolve_token(issued["token"]))

    def test_revoke_user_tokens_revokes_all_sessions(self):
        user = self.store.create_user("alice", "hunter22!")
        first = self.store.issue_token(user["id"])
        second = self.store.issue_token(user["id"])
        self.assertEqual(self.store.revoke_user_tokens(user["id"]), 2)
        self.assertIsNone(self.store.resolve_token(first["token"]))
        self.assertIsNone(self.store.resolve_token(second["token"]))

    def test_revoking_one_user_does_not_affect_another(self):
        alice = self.store.create_user("alice", "hunter22!")
        bob = self.store.create_user("bob", "hunter22!")
        alice_token = self.store.issue_token(alice["id"])
        bob_token = self.store.issue_token(bob["id"])

        self.store.revoke_user_tokens(alice["id"])
        self.assertIsNone(self.store.resolve_token(alice_token["token"]))
        self.assertIsNotNone(self.store.resolve_token(bob_token["token"]))

    def test_purge_expired_sessions(self):
        user = self.store.create_user("alice", "hunter22!")
        self.store.issue_token(user["id"], ttl_days=-1)
        live = self.store.issue_token(user["id"])
        self.assertEqual(self.store.purge_expired_sessions(), 1)
        self.assertIsNotNone(self.store.resolve_token(live["token"]))

    def test_login_upgrades_outdated_hash_parameters(self):
        """
        老参数哈希在登录成功后应被重算。

        这是"把参数写进哈希串"的实际收益：调参数不会锁死老用户。
        """
        user = self.store.create_user("alice", "hunter22!")
        legacy = "scrypt$1024$8$1$"  # 故意用弱参数重写这个用户
        salt = "aa" * 16
        import hashlib

        digest = hashlib.scrypt(
            b"hunter22!", salt=bytes.fromhex(salt), n=1024, r=8, p=1, dklen=32
        )
        with self.store._cursor() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (legacy + salt + "$" + digest.hex(), user["id"]),
            )

        before = self.store.get_user(user["id"])["passwordHash"]
        self.assertEqual(before.split("$")[1], "1024")

        self.assertIsNotNone(self.store.authenticate("alice", "hunter22!"))

        after = self.store.get_user(user["id"])["passwordHash"]
        self.assertEqual(after.split("$")[1], "16384", "登录后应升级到当前参数")
        # 升级后原口令依然有效
        self.assertIsNotNone(self.store.authenticate("alice", "hunter22!"))


class _AuthApiBase(unittest.TestCase):
    """注入临时库的端点测试基类（不启动服务器）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.auth_store = AuthStore(Path(self._tmp.name) / "auth.db")
        self.project_store = ProjectStore(Path(self._tmp.name) / "projects.db")

        self._orig_auth = auth_module.get_auth_store
        self._orig_projects = projects_module.get_store
        auth_module.get_auth_store = lambda: self.auth_store
        projects_module.get_store = lambda: self.project_store

    def tearDown(self):
        auth_module.get_auth_store = self._orig_auth
        projects_module.get_store = self._orig_projects
        self._tmp.cleanup()

    def register(self, username="alice", password="hunter22!"):
        request = auth_module.RegisterRequest(username=username, password=password)
        return asyncio.run(auth_module.register(request))

    def login(self, username="alice", password="hunter22!"):
        request = auth_module.LoginRequest(username=username, password=password)
        return asyncio.run(auth_module.login(request))

    def header(self, token):
        return f"Bearer {token}"

    def current_user(self, token):
        return asyncio.run(auth_module.require_user(self.header(token)))


class AuthApiTest(_AuthApiBase):
    def test_register_returns_token_and_user(self):
        response = self.register()
        self.assertEqual(response.user.username, "alice")
        self.assertTrue(response.token)
        self.assertTrue(response.expiresAt.endswith("+00:00"))

    def test_register_response_never_leaks_password_hash(self):
        response = self.register()
        payload = response.model_dump()
        self.assertNotIn("passwordHash", payload["user"])
        self.assertNotIn("password_hash", repr(payload))

    def test_username_is_normalised_to_lowercase(self):
        self.register("Alice")
        # 用不同大小写登录必须成功（否则会变成两个账号）
        self.assertTrue(self.login("ALICE").token)
        self.assertTrue(self.login(" alice ").token)

    def test_case_variant_registration_is_rejected_as_duplicate(self):
        self.register("alice")
        with self.assertRaises(HTTPException) as ctx:
            self.register("ALICE")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_duplicate_registration_returns_400(self):
        self.register()
        with self.assertRaises(HTTPException) as ctx:
            self.register()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("已被占用", ctx.exception.detail)

    def test_weak_or_invalid_input_returns_422(self):
        with self.assertRaises(ValidationError):
            auth_module.RegisterRequest(username="alice", password="short")
        for bad_username in ["ab", "a" * 33, "has space", "has/slash", "中文名"]:
            with self.subTest(username=bad_username):
                with self.assertRaises(ValidationError):
                    auth_module.RegisterRequest(
                        username=bad_username, password="hunter22!"
                    )

    def test_password_is_not_stripped(self):
        """前后空格是合法口令字符；悄悄去掉会让用户"明明输对了却登不上"。"""
        self.register(password="  spaced pass  ")
        self.assertTrue(self.login(password="  spaced pass  ").token)
        with self.assertRaises(HTTPException):
            self.login(password="spaced pass")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            auth_module.RegisterRequest(
                username="alice", password="hunter22!", role="admin"
            )

    def test_wrong_password_and_unknown_user_give_identical_errors(self):
        """区分两者等于免费提供用户名枚举接口。"""
        self.register()
        with self.assertRaises(HTTPException) as wrong:
            self.login(password="definitely-wrong")
        with self.assertRaises(HTTPException) as missing:
            self.login(username="nobody")
        self.assertEqual(wrong.exception.status_code, 401)
        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(wrong.exception.detail, missing.exception.detail)

    def test_me_requires_and_accepts_token(self):
        response = self.register()
        user = asyncio.run(auth_module.me(self.current_user(response.token)))
        self.assertEqual(user.username, "alice")

    def test_missing_or_bad_token_returns_401_with_challenge(self):
        for header in [None, "", "token-without-scheme", "Bearer wrong", "Basic abc"]:
            with self.subTest(header=header):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(auth_module.require_user(header))
                self.assertEqual(ctx.exception.status_code, 401)
                self.assertEqual(
                    ctx.exception.headers.get("WWW-Authenticate"), "Bearer"
                )

    def test_logout_is_idempotent(self):
        response = self.register()
        first = asyncio.run(auth_module.logout(self.header(response.token)))
        self.assertEqual(first, {"loggedOut": True})
        # 再登出一次（令牌已失效）仍然成功，客户端不该看到错误
        second = asyncio.run(auth_module.logout(self.header(response.token)))
        self.assertEqual(second, {"loggedOut": True})
        # 没有令牌也一样
        self.assertEqual(asyncio.run(auth_module.logout(None)), {"loggedOut": True})

        # 令牌确实失效了
        with self.assertRaises(HTTPException):
            asyncio.run(auth_module.require_user(self.header(response.token)))

    def test_logout_does_not_affect_other_sessions(self):
        first = self.register()
        second = self.login()
        asyncio.run(auth_module.logout(self.header(first.token)))
        # 另一个会话仍然有效
        self.assertIsNotNone(self.current_user(second.token))

    def test_auth_config_is_public(self):
        config = asyncio.run(auth_module.auth_config())
        self.assertTrue(config["allowRegistration"])
        self.assertEqual(config["passwordMinLength"], 8)
        self.assertGreater(config["tokenTtlDays"], 0)


class ProjectOwnershipTest(_AuthApiBase):
    """项目隔离：这是"可协作"与"互相看不见"的分界线。"""

    def setUp(self):
        super().setUp()
        self.alice = self.register("alice")
        self.bob = self.register("bob")

    def _create(self, session, title="项目"):
        request = projects_module.ProjectCreate(title=title)
        return asyncio.run(
            projects_module.create_project(
                request, self.current_user(session.token)
            )
        )

    def _list(self, session):
        return asyncio.run(projects_module.list_projects(
            self.current_user(session.token)
        ))

    def test_new_project_belongs_to_creator(self):
        created = self._create(self.alice)
        self.assertEqual(created.ownerId, self.alice.user.id)

    def test_each_user_only_sees_their_own_projects(self):
        mine = self._create(self.alice, "Alice 的项目")
        self._create(self.bob, "Bob 的项目")

        alice_list = self._list(self.alice)
        bob_list = self._list(self.bob)

        self.assertEqual([p.id for p in alice_list], [mine.id])
        self.assertEqual([p.title for p in bob_list], ["Bob 的项目"])

    def test_reading_someone_elses_project_is_404(self):
        """
        用 404 而不是 403：403 等于确认"这个 id 存在，只是不是你的"，
        可以被用来探测别人有哪些项目。
        """
        mine = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.get_project(
                mine.id, self.current_user(self.bob.token)
            ))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_updating_someone_elses_project_is_404(self):
        mine = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.update_project(
                mine.id,
                projects_module.ProjectUpdate(title="被改掉了"),
                self.current_user(self.bob.token),
            ))
        self.assertEqual(ctx.exception.status_code, 404)
        # 而且真的没有被改
        still = asyncio.run(projects_module.get_project(
            mine.id, self.current_user(self.alice.token)
        ))
        self.assertEqual(still.title, "项目")

    def test_deleting_someone_elses_project_is_404(self):
        mine = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project(
                mine.id, self.current_user(self.bob.token)
            ))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIsNotNone(asyncio.run(projects_module.get_project(
            mine.id, self.current_user(self.alice.token)
        )))

    def test_owner_can_still_do_everything(self):
        mine = self._create(self.alice)
        user = self.current_user(self.alice.token)
        updated = asyncio.run(projects_module.update_project(
            mine.id, projects_module.ProjectUpdate(title="改名了"), user
        ))
        self.assertEqual(updated.title, "改名了")
        asyncio.run(projects_module.delete_project(mine.id, user))
        with self.assertRaises(HTTPException):
            asyncio.run(projects_module.get_project(mine.id, user))

    def test_project_cannot_be_transferred_between_users(self):
        """
        项目不能在用户之间"过户"。**两层都要挡住**：

        - 请求模型 ``extra="forbid"`` → 带 ownerId 的请求直接 422（在到达存储层之前）；
        - 存储层的 ``_UPDATABLE`` 白名单 → 即便有人绕过模型直接调 store，也是 400。

        只挡一层是不够的：第一层防的是接口，第二层防的是"以后有人加了个内部调用"。
        """
        mine = self._create(self.alice)

        # 第一层：请求模型直接拒绝未知字段
        with self.assertRaises(ValidationError):
            projects_module.ProjectUpdate(ownerId=self.bob.user.id)

        # 第二层：存储层白名单
        with self.assertRaises(ValueError) as ctx:
            self.project_store.update(
                mine.id, self.alice.user.id, ownerId=self.bob.user.id
            )
        self.assertIn("ownerId", str(ctx.exception))

        # 归属确实没变
        row = self.project_store.get_project(mine.id, self.alice.user.id)
        self.assertEqual(row["ownerId"], self.alice.user.id)

    def test_unauthorised_access_is_401_not_404(self):
        """没登录 → 401；登录了但不是你的 → 404。两者不能混。"""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(auth_module.require_user(None))
        self.assertEqual(ctx.exception.status_code, 401)


class LegacyProjectTest(_AuthApiBase):
    """
    ``owner_id IS NULL`` 的遗留项目（接上登录之前创建的数据）。

    策略：**对已登录用户可见、不可改、可显式认领**。

    这是**修正后**的设计。初版是"第一个注册的用户自动接管所有无主项目"，
    而 `tools/tasks.py verify` 会注册固定的测试账号——真实验证时它成了第一个
    用户，把开发者手工建的项目静默划给了测试账号（用户下次登录就会发现项目
    不见了）。**静默改变数据归属，比"看得见但要手点一下"危险得多。**
    """

    def test_legacy_project_is_visible_to_any_user(self):
        legacy = self.project_store.create(title="登录之前建的项目")
        self.assertIsNone(legacy["ownerId"])

        session = self.register("first")
        visible = asyncio.run(projects_module.list_projects(
            self.current_user(session.token)
        ))
        self.assertEqual([p.id for p in visible], [legacy["id"]])
        self.assertIsNone(visible[0].ownerId, "仍然未归属：不该被静默划走")

    def test_registering_does_not_change_ownership(self):
        """注册本身**不能**改变任何项目的归属。"""
        legacy = self.project_store.create(title="遗留")
        session = self.register("first")
        self.assertIsNone(
            self.project_store.get_project(legacy["id"], None)["ownerId"]
        )
        self.assertIsNone(
            self.project_store.get_project(legacy["id"], session.user.id)
        )

    def test_legacy_project_cannot_be_modified_before_claiming(self):
        legacy = self.project_store.create(title="遗留")
        session = self.register("first")
        user = self.current_user(session.token)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.update_project(
                legacy["id"], projects_module.ProjectUpdate(title="改了"), user
            ))
        self.assertEqual(ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project(legacy["id"], user))
        self.assertEqual(ctx.exception.status_code, 404)

        # 数据原样还在（谁都没动过它）
        self.assertEqual(
            self.project_store.get_project(legacy["id"], None)["title"], "遗留"
        )

    def test_claim_transfers_ownership_then_editing_works(self):
        legacy = self.project_store.create(title="遗留")
        session = self.register("first")
        user = self.current_user(session.token)

        claimed = asyncio.run(projects_module.claim_project(legacy["id"], user))
        self.assertEqual(claimed.ownerId, session.user.id)
        self.assertEqual(self.project_store.count(None), 0)

        renamed = asyncio.run(projects_module.update_project(
            legacy["id"], projects_module.ProjectUpdate(title="认领后改名"), user
        ))
        self.assertEqual(renamed.title, "认领后改名")

    def test_claim_is_idempotent_for_the_owner(self):
        legacy = self.project_store.create(title="遗留")
        session = self.register("first")
        user = self.current_user(session.token)

        first = asyncio.run(projects_module.claim_project(legacy["id"], user))
        second = asyncio.run(projects_module.claim_project(legacy["id"], user))
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.ownerId, session.user.id)

    def test_claim_cannot_take_someone_elses_project(self):
        """不能靠认领把别人的项目抢过来。"""
        alice = self.register("alice")
        mine = asyncio.run(projects_module.create_project(
            projects_module.ProjectCreate(title="Alice 的"),
            self.current_user(alice.token),
        ))

        bob = self.register("bob")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.claim_project(
                mine.id, self.current_user(bob.token)
            ))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(
            self.project_store.get_project(mine.id, alice.user.id)["ownerId"],
            alice.user.id,
        )

    def test_claim_unknown_project_is_404(self):
        session = self.register("first")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.claim_project(
                "does-not-exist", self.current_user(session.token)
            ))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_claiming_does_not_affect_other_projects(self):
        alice = self.register("alice")
        mine = asyncio.run(projects_module.create_project(
            projects_module.ProjectCreate(title="Alice 的"),
            self.current_user(alice.token),
        ))
        legacy = self.project_store.create(title="遗留")

        bob = self.register("bob")
        bob_user = self.current_user(bob.token)
        asyncio.run(projects_module.claim_project(legacy["id"], bob_user))

        # Alice 的项目仍然属于 Alice，且 bob 的列表里没有它
        self.assertEqual(
            self.project_store.get_project(mine.id, alice.user.id)["ownerId"],
            alice.user.id,
        )
        self.assertEqual(
            [p.title for p in asyncio.run(projects_module.list_projects(bob_user))],
            ["遗留"],
        )


if __name__ == "__main__":
    unittest.main()
