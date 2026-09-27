"""
项目共享测试。

在此之前所有人只能看到自己的项目——"协作"二字缺的正是"把项目交给别人"。
这一组测试覆盖四层：

1. `AccessRoleTest`——**权限判定的唯一入口** `access_role` 的真值表；
2. `SharesStoreTest`——共享记录的增删改查与"不堆重复条目"；
3. `SharingApiTest`——端点语义：谁能共享、谁不能，以及 viewer/editor 的能力边界；
4. `ShareIsolationTest`——被共享者**看不到**不共享给他的东西，也改不了属主的东西。

权限这种东西最容易出的问题是"某处忘了判断"，所以这里对**每一个写端点**
都同时断言"有权限成功"与"没权限失败"。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import auth as auth_module
import project_store as project_store_module
import projects as projects_module
from auth_store import AuthStore
from project_store import (
    ROLE_OWNER,
    ROLE_UNOWNED,
    ProjectStore,
)


class AccessRoleTest(unittest.TestCase):
    """`access_role` 是权限判定的唯一入口，先把它的真值表钉住。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "roles.db")
        self.mine = self.store.create(title="我的", owner_id="alice")
        self.other = self.store.create(title="别人的", owner_id="bob")
        self.orphan = self.store.create(title="无主的")

    def tearDown(self):
        self._tmp.cleanup()

    def test_owner_editor_viewer_stranger(self):
        self.store.share(self.mine["id"], "carol", "editor")
        self.store.share(self.mine["id"], "dave", "viewer")

        self.assertEqual(self.store.access_role(self.mine["id"], "alice"), ROLE_OWNER)
        self.assertEqual(self.store.access_role(self.mine["id"], "carol"), "editor")
        self.assertEqual(self.store.access_role(self.mine["id"], "dave"), "viewer")
        self.assertIsNone(self.store.access_role(self.mine["id"], "eve"))
        self.assertIsNone(self.store.access_role(self.mine["id"], None))

    def test_other_owners_project_is_not_accessible(self):
        self.assertIsNone(self.store.access_role(self.other["id"], "alice"))

    def test_unowned_project_is_visible_but_that_is_not_a_role(self):
        """``unowned`` 是"没有属主"这一事实，不是一种角色。"""
        self.assertEqual(
            self.store.access_role(self.orphan["id"], "alice"), ROLE_UNOWNED
        )
        self.assertTrue(self.store.can_read(ROLE_UNOWNED))
        # 关键：不能通过它获得任何写权限
        self.assertFalse(self.store.can_edit(ROLE_UNOWNED))
        self.assertFalse(self.store.can_manage(ROLE_UNOWNED))

    def test_missing_project_is_none(self):
        self.assertIsNone(self.store.access_role("nope", "alice"))

    def test_permission_truth_table(self):
        cases = {
            ROLE_OWNER: (True, True, True),
            "editor": (True, True, False),
            "viewer": (True, False, False),
            ROLE_UNOWNED: (True, False, False),
            None: (False, False, False),
        }
        for role, (read, edit, manage) in cases.items():
            with self.subTest(role=role):
                self.assertEqual(self.store.can_read(role), read)
                self.assertEqual(self.store.can_edit(role), edit)
                self.assertEqual(self.store.can_manage(role), manage)


class SharesStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "shares.db")
        self.project = self.store.create(title="共享测试", owner_id="alice")

    def tearDown(self):
        self._tmp.cleanup()

    def test_share_then_list(self):
        record = self.store.share(self.project["id"], "bob", "viewer", invited_by="alice")
        self.assertEqual(record["role"], "viewer")
        self.assertEqual(record["invitedBy"], "alice")
        shares = self.store.list_shares(self.project["id"])
        self.assertEqual([item["userId"] for item in shares], ["bob"])

    def test_resharing_updates_role_without_duplicates(self):
        """
        同一个 (项目, 用户) 只能有一条记录。

        堆出重复条目会让"取消共享"说不清该删哪条，也会让"他到底是什么角色"
        取决于行的顺序——这是复合主键 + ON CONFLICT 要防的事。
        """
        self.store.share(self.project["id"], "bob", "viewer")
        self.store.share(self.project["id"], "bob", "editor")
        shares = self.store.list_shares(self.project["id"])
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0]["role"], "editor")
        self.assertEqual(self.store.access_role(self.project["id"], "bob"), "editor")

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.share(self.project["id"], "bob", "admin")

    def test_unshare(self):
        self.store.share(self.project["id"], "bob", "viewer")
        self.assertTrue(self.store.unshare(self.project["id"], "bob"))
        self.assertFalse(self.store.unshare(self.project["id"], "bob"))
        self.assertIsNone(self.store.access_role(self.project["id"], "bob"))

    def test_unshare_does_not_touch_the_project(self):
        self.store.share(self.project["id"], "bob", "editor")
        self.store.unshare(self.project["id"], "bob")
        row = self.store.get_project(self.project["id"], "alice")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "共享测试")

    def test_shares_survive_reopen(self):
        self.store.share(self.project["id"], "bob", "editor")
        reopened = ProjectStore(self.store.db_path)
        self.assertEqual(reopened.access_role(self.project["id"], "bob"), "editor")

    # ---- 列表按"能看到什么"组织 ----

    def test_list_visible_includes_own_shared_and_unowned(self):
        orphan = self.store.create(title="无主的")           # 无属主
        other = self.store.create(title="别人的", owner_id="bob")
        self.store.share(other["id"], "alice", "viewer")     # bob 共享给 alice

        visible = self.store.list_visible("alice")
        by_title = {item["title"]: item for item in visible}

        self.assertEqual(by_title["共享测试"]["role"], "owner")
        self.assertEqual(by_title["无主的"]["role"], "unowned")
        self.assertEqual(by_title["别人的"]["role"], "viewer")

    def test_list_visible_puts_own_projects_first(self):
        self.store.create(title="别人的", owner_id="bob")
        self.store.share(self.store.list_projects("bob")[0]["id"], "alice", "viewer")
        visible = self.store.list_visible("alice")
        self.assertEqual(visible[0]["role"], "owner")

    def test_list_visible_excludes_projects_without_access(self):
        self.store.create(title="和我无关的", owner_id="bob")
        titles = [item["title"] for item in self.store.list_visible("alice")]
        self.assertNotIn("和我无关的", titles)

    def test_get_visible_attaches_role(self):
        self.store.share(self.project["id"], "bob", "editor")
        row = self.store.get_visible(self.project["id"], "bob")
        self.assertEqual(row["role"], "editor")
        self.assertIsNone(self.store.get_visible(self.project["id"], "eve"))


class _SharingApiBase(unittest.TestCase):
    """注入临时库 + 两个用户，直接调用端点函数（不启动服务器）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.auth_store = AuthStore(root / "auth.db")
        self.project_store = ProjectStore(root / "projects.db")

        self._orig = (
            projects_module.get_store, projects_module.get_auth_store,
            auth_module.get_auth_store,
        )
        projects_module.get_store = lambda: self.project_store
        projects_module.get_auth_store = lambda: self.auth_store
        auth_module.get_auth_store = lambda: self.auth_store

        self.alice = self._register("alice")
        self.bob = self._register("bob")
        self.carol = self._register("carol")

    def tearDown(self):
        (projects_module.get_store, projects_module.get_auth_store,
         auth_module.get_auth_store) = self._orig
        self._tmp.cleanup()

    # ------------------------------------------------------------- 工具
    def _register(self, username):
        request = auth_module.RegisterRequest(username=username, password="hunter22!")
        return asyncio.run(auth_module.register(request))

    def _user(self, session):
        return asyncio.run(auth_module.require_user(f"Bearer {session.token}"))

    def _create(self, session, title="共享项目"):
        return asyncio.run(projects_module.create_project(
            projects_module.ProjectCreate(title=title), self._user(session)
        ))

    def _share(self, session, project_id, username, role="viewer"):
        return asyncio.run(projects_module.create_project_share(
            project_id,
            projects_module.ShareInvite(username=username, role=role),
            self._user(session),
        ))

    def _list(self, session):
        return asyncio.run(projects_module.list_projects(self._user(session)))

    def _put_setup(self, session, project_id, **overrides):
        payload = {"version": 1, "materialId": "structural_steel"}
        payload.update(overrides)
        return asyncio.run(projects_module.put_project_setup(
            project_id,
            projects_module.SimulationSetup(**payload),
            self._user(session),
        ))


class SharingApiTest(_SharingApiBase):
    def test_owner_shares_and_sharee_sees_it(self):
        project = self._create(self.alice)
        share = self._share(self.alice, project.id, "bob", "viewer")
        self.assertEqual(share.username, "bob")
        self.assertEqual(share.role, "viewer")

        bob_view = self._list(self.bob)
        self.assertEqual([(item.id, item.role) for item in bob_view],
                         [(project.id, "viewer")])
        # 属主自己的列表里 role 是 owner
        self.assertEqual(self._list(self.alice)[0].role, "owner")

    def test_share_response_never_leaks_password_hash(self):
        project = self._create(self.alice)
        share = self._share(self.alice, project.id, "bob")
        self.assertNotIn("password", repr(share.model_dump()).lower())

    def test_share_with_unknown_username_is_404(self):
        project = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            self._share(self.alice, project.id, "nobody-here")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cannot_share_with_yourself(self):
        project = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            self._share(self.alice, project.id, "alice")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_role_is_422(self):
        with self.assertRaises(ValidationError):
            projects_module.ShareInvite(username="bob", role="admin")

    def test_non_owner_cannot_share(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob", "editor")
        # bob 是 editor，仍然不能把项目再共享给别人
        with self.assertRaises(HTTPException) as ctx:
            self._share(self.bob, project.id, "carol")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_only_owner_sees_the_share_list(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob")
        listing = asyncio.run(projects_module.list_project_shares(
            project.id, self._user(self.alice)
        ))
        self.assertEqual([item.username for item in listing], ["bob"])

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.list_project_shares(
                project.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_resharing_updates_the_role(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob", "viewer")
        self._share(self.alice, project.id, "bob", "editor")
        listing = asyncio.run(projects_module.list_project_shares(
            project.id, self._user(self.alice)
        ))
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0].role, "editor")
        self.assertEqual(self._list(self.bob)[0].role, "editor")

    def test_owner_can_remove_a_collaborator(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob")
        result = asyncio.run(projects_module.delete_project_share(
            project.id, self.bob.user.id, self._user(self.alice)
        ))
        self.assertEqual(result["removed"], True)
        self.assertEqual(self._list(self.bob), [])

    def test_collaborator_can_remove_themselves(self):
        """否则被共享者没法退出一个共享。"""
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob")
        asyncio.run(projects_module.delete_project_share(
            project.id, self.bob.user.id, self._user(self.bob)
        ))
        self.assertEqual(self._list(self.bob), [])
        # 属主侧不受影响
        self.assertEqual(self._list(self.alice)[0].id, project.id)

    def test_removing_someone_elses_share_is_403(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob")
        self._share(self.alice, project.id, "carol")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project_share(
                project.id, self.carol.user.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_removing_a_share_that_does_not_exist_is_404(self):
        project = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project_share(
                project.id, self.bob.user.id, self._user(self.alice)
            ))
        self.assertEqual(ctx.exception.status_code, 404)


class SharePermissionsTest(_SharingApiBase):
    """
    viewer / editor 的能力边界。

    对**每一个写操作**都同时断言"该成功的成功、该失败的失败"——
    权限的漏洞几乎总是"某处忘了判断"，只测正向是发现不了的。
    """

    def setUp(self):
        super().setUp()
        self.project = self._create(self.alice)

    # ---- 读：三种角色都能读 ----

    def test_viewer_and_editor_can_read_project_and_setup(self):
        self._put_setup(self.alice, self.project.id)
        for who, role in ((self.bob, "viewer"), (self.carol, "editor")):
            self._share(self.alice, self.project.id, who.user.username, role)
            with self.subTest(role=role):
                fetched = asyncio.run(projects_module.get_project(
                    self.project.id, self._user(who)
                ))
                self.assertEqual(fetched.id, self.project.id)
                setup = asyncio.run(projects_module.get_project_setup(
                    self.project.id, self._user(who)
                ))
                self.assertEqual(setup.setup.materialId, "structural_steel")

    def test_stranger_cannot_read(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.get_project(
                self.project.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 404)

    # ---- 写配置：viewer 不行，editor 行 ----

    def test_viewer_cannot_write_setup(self):
        self._share(self.alice, self.project.id, "bob", "viewer")
        with self.assertRaises(HTTPException) as ctx:
            self._put_setup(self.bob, self.project.id)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("只读", ctx.exception.detail)

    def test_editor_can_write_setup(self):
        self._share(self.alice, self.project.id, "bob", "editor")
        saved = self._put_setup(self.bob, self.project.id, materialId="copper")
        self.assertEqual(saved.setup.materialId, "copper")
        # 属主看得到 editor 改的内容——这就是"一起做"的含义
        owner_view = asyncio.run(projects_module.get_project_setup(
            self.project.id, self._user(self.alice)
        ))
        self.assertEqual(owner_view.setup.materialId, "copper")

    def test_stranger_cannot_write_setup(self):
        with self.assertRaises(HTTPException) as ctx:
            self._put_setup(self.bob, self.project.id)
        self.assertEqual(ctx.exception.status_code, 404)

    # ---- 元信息与删除：只有属主 ----

    def test_only_owner_can_rename(self):
        self._share(self.alice, self.project.id, "bob", "editor")
        renamed = asyncio.run(projects_module.update_project(
            self.project.id, projects_module.ProjectUpdate(title="属主改名"),
            self._user(self.alice),
        ))
        self.assertEqual(renamed.title, "属主改名")

        for who, role in ((self.bob, "editor"), (self.carol, "viewer")):
            self._share(self.alice, self.project.id, who.user.username, role)
            with self.subTest(role=role):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(projects_module.update_project(
                        self.project.id, projects_module.ProjectUpdate(title="我也来改"),
                        self._user(who),
                    ))
                self.assertEqual(ctx.exception.status_code, 404)

    def test_only_owner_can_delete(self):
        self._share(self.alice, self.project.id, "bob", "editor")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project(
                self.project.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 404)
        # 项目还在
        self.assertIsNotNone(asyncio.run(projects_module.get_project(
            self.project.id, self._user(self.alice)
        )))

    def test_only_owner_can_clear_setup(self):
        """清空是破坏性的（会丢掉别人配好的东西），不因为"是 editor"就放行。"""
        self._put_setup(self.alice, self.project.id)
        self._share(self.alice, self.project.id, "bob", "editor")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project_setup(
                self.project.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 403)
        # 属主可以
        asyncio.run(projects_module.delete_project_setup(
            self.project.id, self._user(self.alice)
        ))
        after = asyncio.run(projects_module.get_project_setup(
            self.project.id, self._user(self.alice)
        ))
        self.assertIsNone(after.setup)


class ShareIsolationTest(_SharingApiBase):
    """共享不等于"看到全部"——被共享者只能看到共享给他的那些。"""

    def test_sharee_does_not_see_other_projects(self):
        shared = self._create(self.alice, "共享给 bob 的")
        private = self._create(self.alice, "没共享给 bob 的")
        self._share(self.alice, shared.id, "bob")

        bob_ids = [item.id for item in self._list(self.bob)]
        self.assertIn(shared.id, bob_ids)
        self.assertNotIn(private.id, bob_ids)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.get_project(
                private.id, self._user(self.bob)
            ))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_revoking_access_takes_effect_immediately(self):
        project = self._create(self.alice)
        self._share(self.alice, project.id, "bob", "editor")
        self.assertEqual(len(self._list(self.bob)), 1)

        asyncio.run(projects_module.delete_project_share(
            project.id, self.bob.user.id, self._user(self.alice)
        ))
        self.assertEqual(self._list(self.bob), [])
        with self.assertRaises(HTTPException) as ctx:
            self._put_setup(self.bob, project.id)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_share_and_project_are_scoped_to_the_right_owner(self):
        """bob 的项目共享给 alice 之后，alice 也不能改 bob 的项目元信息。"""
        bobs_project = self._create(self.bob, "bob 的项目")
        self._share(self.bob, bobs_project.id, "alice", "editor")

        alice_view = self._list(self.alice)
        entry = next(item for item in alice_view if item.id == bobs_project.id)
        self.assertEqual(entry.role, "editor")

        # editor 能改配置
        self._put_setup(self.alice, bobs_project.id, materialId="aluminum_alloy")
        # 但不能改名/删除
        with self.assertRaises(HTTPException):
            asyncio.run(projects_module.update_project(
                bobs_project.id, projects_module.ProjectUpdate(title="我改了"),
                self._user(self.alice),
            ))
        with self.assertRaises(HTTPException):
            asyncio.run(projects_module.delete_project(
                bobs_project.id, self._user(self.alice)
            ))


if __name__ == "__main__":
    unittest.main()
