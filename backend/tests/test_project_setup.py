"""
项目仿真配置持久化测试。

回归点：在此之前"项目"只是一个名字 + 描述——几何、材料、边界条件、网格与求解
设置**全在浏览器内存里**，关掉页面就没了。于是"项目"这个概念的承诺（下次接着做）
实际上是空的：重新打开项目是一个空白工作台。

这组测试覆盖五层：
1. `SetupStoreTest`——存储：往返、覆盖、清空、**属主隔离**、坏 JSON 的兜底；
2. `SetupValidationTest`——校验策略是**分级**的（后端要消费的字段从严，
   纯界面设置从宽但有界），每一条都要有明确的理由；
3. `SetupApiTest`——端点语义：404（项目不属于你）与 200+null（还没配过）必须分开；
4. `SetupConcurrencyTest`——**乐观并发控制**：过期保存必须被拒绝，而且
   **不能改动库里的内容**（这是"不再静默丢数据"的全部意义）；
5. `SetupMigrationTest`——老库自动补 `setup` / `setup_version` 列且旧数据不丢。
"""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import project_store as project_store_module
import projects as projects_module
from project_store import ProjectStore, SetupConflict


def _setup_payload(**overrides):
    """一份正常的配置（形状与前端 `buildSetupPayload` 一致）。"""
    payload = {
        "version": 1,
        "geometryFilename": "test_part.step",
        "materialId": "structural_steel",
        "boundaryConditions": [
            {
                "id": "bc_fix", "name": "固定端", "type": "fixed",
                "applicationType": "face", "entityIndex": 1, "color": "#ff0000",
            },
            {
                "id": "bc_load", "name": "拉力", "type": "force",
                "applicationType": "face", "entityIndex": 2, "color": "#00ff00",
                "force": {"x": 1000.0, "y": 0.0, "z": 0.0},
            },
        ],
        "meshSettings": {
            "id": "mesh_1", "name": "默认网格", "meshType": "tetrahedral",
            "meshSize": 1.5, "refinementRegions": [], "quality": 0.8,
            "status": "meshed",
        },
        "solverSettings": {
            "id": "solver_1", "name": "Solver - structural",
            "solverType": "structural", "solverName": "default",
            "parameters": {"timeStep": 0.1}, "lengthUnit": "mm",
            "status": "solved",
        },
    }
    payload.update(overrides)
    return payload


class SetupStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "setup.db")
        self.project = self.store.create(title="配置测试", owner_id="user-a")
        self.other = self.store.create(title="别人的", owner_id="user-b")

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_project_has_no_setup(self):
        self.assertEqual(self.project["hasSetup"], False)
        self.assertEqual(
            self.store.get_setup(self.project["id"], "user-a"),
            {"setup": None, "savedAt": None, "version": 0},
        )

    def test_save_and_read_back(self):
        saved = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="user-a"
        )
        self.assertIsNotNone(saved)
        self.assertEqual(saved["setup"]["materialId"], "structural_steel")
        self.assertEqual(len(saved["setup"]["boundaryConditions"]), 2)
        self.assertTrue(saved["savedAt"])

        refetched = self.store.get_setup(self.project["id"], "user-a")
        self.assertEqual(refetched["setup"], saved["setup"])

    def test_has_setup_flag_appears_in_list(self):
        self.assertFalse(self.store.list_projects("user-a")[0]["hasSetup"])
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="user-a")
        self.assertTrue(self.store.list_projects("user-a")[0]["hasSetup"])

    def test_save_is_a_full_replacement(self):
        """整份覆盖：第二次保存只剩一个边界条件，不能残留第一次的。"""
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="user-a")
        fewer = _setup_payload(boundaryConditions=[
            {"id": "only", "name": "唯一", "type": "fixed",
             "applicationType": "face", "entityIndex": 1, "color": "#ff0000"}
        ])
        saved = self.store.set_setup(self.project["id"], fewer, owner_id="user-a")
        self.assertEqual(len(saved["setup"]["boundaryConditions"]), 1)

    def test_clear_setup_keeps_the_project(self):
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="user-a")
        self.assertTrue(self.store.clear_setup(self.project["id"], owner_id="user-a"))
        self.assertEqual(
            self.store.get_setup(self.project["id"], "user-a")["setup"], None
        )
        # 项目本身还在
        self.assertIsNotNone(self.store.get_project(self.project["id"], "user-a"))

    def test_setup_is_scoped_to_owner(self):
        """别人的项目：读不到、写不了——**不是**"读到一个空配置"。"""
        self.assertIsNone(self.store.set_setup(self.project["id"], {}, owner_id="user-b"))
        self.assertIsNone(self.store.get_setup(self.project["id"], "user-b"))
        self.assertFalse(self.store.clear_setup(self.project["id"], owner_id="user-b"))
        # 原属主的数据没被动过
        self.assertEqual(
            self.store.get_setup(self.project["id"], "user-a")["setup"], None
        )

    def test_setup_survives_reopen(self):
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="user-a")
        reopened = ProjectStore(self.store.db_path)
        self.assertEqual(
            reopened.get_setup(self.project["id"], "user-a")["setup"]["geometryFilename"],
            "test_part.step",
        )

    def test_unicode_survives_round_trip(self):
        payload = _setup_payload(geometryFilename="零件1.step")
        saved = self.store.set_setup(self.project["id"], payload, owner_id="user-a")
        self.assertEqual(saved["setup"]["geometryFilename"], "零件1.step")

    def test_corrupt_json_is_treated_as_empty_not_a_crash(self):
        """
        库里存的不是合法 JSON 时，应如实按"空配置"返回并留日志，
        而不是让端点 500——500 的话用户完全不知道发生了什么，
        也不知道配置其实已经丢了。
        """
        with self.store._cursor() as connection:
            connection.execute(
                "UPDATE projects SET setup = ? WHERE id = ?",
                ("{ 这不是 JSON", self.project["id"]),
            )
        self.assertEqual(
            self.store.get_setup(self.project["id"], "user-a"),
            {"setup": None, "savedAt": None, "version": 0},
        )
        self.assertFalse(self.store.list_projects("user-a")[0]["hasSetup"])


class SetupValidationTest(unittest.TestCase):
    """
    校验策略是**分级**的：后端要消费的字段从严，纯界面设置从宽但有界。
    """

    def _parse(self, **overrides):
        return projects_module.SimulationSetup(**_setup_payload(**overrides))

    def test_accepts_the_frontend_shape(self):
        setup = self._parse()
        self.assertEqual(setup.version, 1)
        self.assertEqual(len(setup.boundaryConditions), 2)
        self.assertEqual(setup.solverSettings["lengthUnit"], "mm")

    def test_defaults_allow_an_empty_project(self):
        setup = projects_module.SimulationSetup()
        self.assertEqual(setup.version, 1)
        self.assertIsNone(setup.geometryFilename)
        self.assertIsNone(setup.materialId)
        self.assertEqual(setup.boundaryConditions, [])

    # ---- 从严：后端自己要消费的字段 ----

    def test_geometry_filename_must_be_a_plain_supported_file(self):
        for bad in ["../../etc/passwd.step", "sub/dir/a.step", "a.exe", "a.txt", ""]:
            with self.subTest(filename=bad):
                with self.assertRaises(ValidationError):
                    self._parse(geometryFilename=bad)

    def test_geometry_filename_is_allowed_to_not_exist_yet(self):
        """文件可能已被清理，但配置仍应能保存——不该因此丢掉用户的边界条件。"""
        setup = self._parse(geometryFilename="not_uploaded_yet.step")
        self.assertEqual(setup.geometryFilename, "not_uploaded_yet.step")

    def test_boundary_conditions_are_strictly_validated(self):
        # 未知字段必须报错（与 BoundaryCondition 的 extra="forbid" 一致）
        with self.assertRaises(ValidationError):
            self._parse(boundaryConditions=[
                {"id": "x", "name": "x", "type": "fixed", "applicationType": "face",
                 "entityIndex": 1, "color": "#fff", "typo_field": 1}
            ])
        # 非法类型必须报错
        with self.assertRaises(ValidationError):
            self._parse(boundaryConditions=[
                {"id": "x", "name": "x", "type": "not_a_type",
                 "applicationType": "face", "entityIndex": 1, "color": "#fff"}
            ])

    def test_boundary_condition_count_is_capped(self):
        from config import SIMULATION_SETUP_MAX_BCS

        one = {"id": "x", "name": "x", "type": "fixed",
               "applicationType": "face", "entityIndex": 1, "color": "#fff"}
        with self.assertRaises(ValidationError):
            self._parse(boundaryConditions=[dict(one, id=f"bc{i}")
                                            for i in range(SIMULATION_SETUP_MAX_BCS + 1)])
        # 刚好达到上限是允许的
        self.assertEqual(
            len(self._parse(boundaryConditions=[
                dict(one, id=f"bc{i}") for i in range(SIMULATION_SETUP_MAX_BCS)
            ]).boundaryConditions),
            SIMULATION_SETUP_MAX_BCS,
        )

    # ---- 从宽但有界：纯前端界面设置 ----

    def test_ui_settings_are_not_field_by_field_validated(self):
        """
        `meshSettings`/`solverSettings` 属于界面状态，形状会随界面迭代变化。
        在这里逐字段钉死会把后端与前端 UI 耦合起来（每次改界面都要同时改后端），
        所以只要求是 JSON 对象。
        """
        setup = self._parse(
            meshSettings={"brand_new_field": 123, "whatever": {"nested": True}},
            solverSettings={"analysisType": "将来才有的类型"},
        )
        self.assertEqual(setup.meshSettings["brand_new_field"], 123)
        self.assertEqual(setup.solverSettings["analysisType"], "将来才有的类型")

    def test_version_must_be_one(self):
        with self.assertRaises(ValidationError):
            self._parse(version=99)


class SetupApiTest(unittest.TestCase):
    """端点层：注入临时库，验证 404 与 200+null 必须分开。"""

    USER = {
        "id": "user-test",
        "username": "tester",
        "displayName": "Tester",
        "createdAt": "2026-03-18T00:00:00+00:00",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "api.db")
        self._original = project_store_module.get_store
        project_store_module.get_store = lambda: self.store
        projects_module.get_store = lambda: self.store
        created = asyncio.run(projects_module.create_project(
            projects_module.ProjectCreate(title="项目"), self.USER
        ))
        self.project_id = created.id

    def tearDown(self):
        project_store_module.get_store = self._original
        projects_module.get_store = self._original
        self._tmp.cleanup()

    def test_get_before_saving_returns_null_not_404(self):
        response = asyncio.run(projects_module.get_project_setup(
            self.project_id, self.USER
        ))
        self.assertIsNone(response.setup)
        self.assertIsNone(response.savedAt)

    def test_get_unknown_project_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.get_project_setup("nope", self.USER))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_put_then_get_round_trip(self):
        request = projects_module.SimulationSetup(**_setup_payload())
        saved = asyncio.run(projects_module.put_project_setup(
            self.project_id, request, self.USER
        ))
        self.assertEqual(saved.setup.materialId, "structural_steel")
        self.assertTrue(saved.savedAt)

        fetched = asyncio.run(projects_module.get_project_setup(
            self.project_id, self.USER
        ))
        self.assertEqual(fetched.setup.geometryFilename, "test_part.step")
        self.assertEqual(len(fetched.setup.boundaryConditions), 2)

    def test_put_unknown_project_returns_404(self):
        request = projects_module.SimulationSetup()
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.put_project_setup("nope", request, self.USER))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_put_marks_project_as_configured_in_the_list(self):
        before = asyncio.run(projects_module.list_projects(self.USER))
        self.assertFalse(before[0].hasSetup)

        asyncio.run(projects_module.put_project_setup(
            self.project_id, projects_module.SimulationSetup(), self.USER
        ))
        after = asyncio.run(projects_module.list_projects(self.USER))
        self.assertTrue(after[0].hasSetup)

    def test_delete_setup_keeps_the_project(self):
        asyncio.run(projects_module.put_project_setup(
            self.project_id, projects_module.SimulationSetup(**_setup_payload()), self.USER
        ))
        result = asyncio.run(projects_module.delete_project_setup(
            self.project_id, self.USER
        ))
        self.assertEqual(result, {"cleared": True, "id": self.project_id})
        # 项目还在，只是没配置了
        still = asyncio.run(projects_module.get_project(self.project_id, self.USER))
        self.assertEqual(still.id, self.project_id)
        self.assertFalse(still.hasSetup)

    def test_oversized_setup_returns_413(self):
        from config import SIMULATION_SETUP_MAX_BYTES

        big = "x" * (SIMULATION_SETUP_MAX_BYTES + 1000)
        request = projects_module.SimulationSetup(
            meshSettings={"blob": big}
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.put_project_setup(
                self.project_id, request, self.USER
            ))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("过大", ctx.exception.detail)

    def test_setup_is_not_returned_in_the_project_list(self):
        """
        列表接口**不带**完整配置：配置可能几十 KB，塞进列表会让响应成倍变大。
        """
        asyncio.run(projects_module.put_project_setup(
            self.project_id, projects_module.SimulationSetup(**_setup_payload()), self.USER
        ))
        listing = asyncio.run(projects_module.list_projects(self.USER))
        dumped = listing[0].model_dump()
        self.assertNotIn("setup", dumped)
        self.assertTrue(dumped["hasSetup"])


class SetupConcurrencyTest(unittest.TestCase):
    """
    乐观并发控制：把"两个人同时改"从**静默丢数据**变成**显式冲突**。

    最要紧的一条断言不是"返回 409"，而是"**冲突时库里的内容没变**"——
    409 只是手段，"先保存的人的工作没被抹掉"才是目的。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "concurrency.db")
        # 端点用的是**模块级**的 get_store()，而 `from … import get_store` 是导入时
        # 绑定函数对象的，所以 projects_module 与 project_store_module 两份引用都要
        # 替换——只换一份的话端点会去开真实库，表现为莫名其妙的 404。
        self._original = (
            projects_module.get_store, project_store_module.get_store,
        )
        projects_module.get_store = lambda: self.store
        project_store_module.get_store = lambda: self.store
        self.project = self.store.create(title="并发", owner_id="owner-1")

    def tearDown(self):
        (projects_module.get_store, project_store_module.get_store) = self._original
        self._tmp.cleanup()

    # ------------------------------------------------------------- 版本号
    def test_version_starts_at_zero_and_increments(self):
        self.assertEqual(
            self.store.get_setup(self.project["id"], "owner-1")["version"], 0
        )
        first = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="owner-1"
        )
        self.assertEqual(first["version"], 1)
        second = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="owner-1"
        )
        self.assertEqual(second["version"], 2)

    def test_unconditional_save_ignores_version(self):
        """不带版本号 = 无条件覆盖（老客户端/脚本的兼容路径）。"""
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="owner-1")
        saved = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="owner-1",
            expected_version=None,
        )
        self.assertEqual(saved["version"], 2)

    # ------------------------------------------------------------- 冲突
    def test_matching_version_is_accepted(self):
        first = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="owner-1"
        )
        saved = self.store.set_setup(
            self.project["id"], _setup_payload(materialId="aluminum_6061"),
            owner_id="owner-1", expected_version=first["version"],
        )
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["setup"]["materialId"], "aluminum_6061")

    def test_stale_version_is_rejected_and_storage_is_untouched(self):
        """
        过期保存必须被拒，**而且库里的内容一个字都不能改**。

        这是这一轮的全部意义：在此之前，后保存的人会静默覆盖先保存的人。
        """
        first = self.store.set_setup(
            self.project["id"], _setup_payload(materialId="structural_steel"),
            owner_id="owner-1",
        )
        # 另一个人（editor）在同一基线上改了一次
        self.store.set_setup(
            self.project["id"], _setup_payload(materialId="aluminum_6061"),
            owner_id="owner-1", expected_version=first["version"],
        )

        with self.assertRaises(SetupConflict) as ctx:
            self.store.set_setup(
                self.project["id"], _setup_payload(materialId="titanium"),
                owner_id="owner-1", expected_version=first["version"],
            )
        self.assertEqual(ctx.exception.current_version, 2)

        # ★ 关键：内容仍然是别人写进去的那一份，titanium 没有被写进去
        stored = self.store.get_setup(self.project["id"], "owner-1")
        self.assertEqual(stored["setup"]["materialId"], "aluminum_6061")
        self.assertEqual(stored["version"], 2)

    def test_conflict_reports_the_current_version(self):
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="owner-1")
        with self.assertRaises(SetupConflict) as ctx:
            self.store.set_setup(
                self.project["id"], _setup_payload(), owner_id="owner-1",
                expected_version=0,
            )
        # 客户端据此立刻重试（或者先看清差异）
        self.assertEqual(ctx.exception.current_version, 1)

    def test_unknown_project_is_not_a_conflict(self):
        """项目不存在 / 不属于该属主时仍是 `None`（404），不是冲突（409）。"""
        result = self.store.set_setup(
            "nope", _setup_payload(), owner_id="owner-1", expected_version=0
        )
        self.assertIsNone(result)
        result = self.store.set_setup(
            self.project["id"], _setup_payload(), owner_id="someone-else",
            expected_version=0,
        )
        self.assertIsNone(result)

    def test_clear_also_bumps_the_version(self):
        """
        清空也是一次修改，必须 +1。

        不 +1 的话，正在编辑的人（拿着旧版本号）会在清空之后成功保存，
        把"清空"这件事悄悄抹掉——那正是本轮要消除的静默覆盖。
        """
        self.store.set_setup(self.project["id"], _setup_payload(), owner_id="owner-1")
        self.assertTrue(self.store.clear_setup(self.project["id"], "owner-1"))
        after_clear = self.store.get_setup(self.project["id"], "owner-1")
        self.assertIsNone(after_clear["setup"])
        self.assertEqual(after_clear["version"], 2)
        with self.assertRaises(SetupConflict):
            self.store.set_setup(
                self.project["id"], _setup_payload(), owner_id="owner-1",
                expected_version=1,
            )

    # ------------------------------------------------------------- 端点
    def test_parse_if_match_accepts_the_usual_spellings(self):
        """`3`、`"3"`、`W/"3"` 都要认（不同客户端写法不同）。"""
        for raw in ("3", '"3"', 'W/"3"', " 3 ", 'W/"3" '):
            with self.subTest(raw=raw):
                self.assertEqual(projects_module.parse_if_match(raw), 3)
        self.assertIsNone(projects_module.parse_if_match(None))
        self.assertIsNone(projects_module.parse_if_match(""))
        self.assertIsNone(projects_module.parse_if_match("   "))
        # 直接调用端点函数时拿到的是 FastAPI 的 Header 标记对象，不是字符串
        self.assertIsNone(projects_module.parse_if_match(object()))

    def test_parse_if_match_rejects_garbage_instead_of_ignoring_it(self):
        """
        解析不出来必须返回 `-1`（调用方转 400），**不能当成"没有条件"**。

        当成"没有条件"会把一个写错的头静默降级成无条件覆盖——正好丢掉这一轮
        要建立的安全保障。
        """
        for raw in ("abc", "1.5", "-1", "3x", '"'):
            with self.subTest(raw=raw):
                self.assertEqual(projects_module.parse_if_match(raw), -1)

    def test_endpoint_saves_and_returns_the_new_version(self):
        request = projects_module.SimulationSetup(**_setup_payload())
        saved = asyncio.run(
            projects_module.put_project_setup(
                self.project["id"], request, user={"id": "owner-1"},
                if_match='"0"',
            )
        )
        self.assertEqual(saved.version, 1)

    def test_endpoint_rejects_a_stale_write_with_409(self):
        request = projects_module.SimulationSetup(**_setup_payload())
        asyncio.run(
            projects_module.put_project_setup(
                self.project["id"], request, user={"id": "owner-1"}, if_match='"0"',
            )
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                projects_module.put_project_setup(
                    self.project["id"], request, user={"id": "owner-1"},
                    if_match='"0"',
                )
            )
        self.assertEqual(ctx.exception.status_code, 409)
        # 冲突响应带上当前版本，客户端据此重试
        self.assertEqual(ctx.exception.headers.get("ETag"), '"1"')

    def test_endpoint_rejects_a_malformed_header_with_400(self):
        request = projects_module.SimulationSetup(**_setup_payload())
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                projects_module.put_project_setup(
                    self.project["id"], request, user={"id": "owner-1"},
                    if_match="not-a-version",
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)
        # 400 而不是"当成无条件写入"：内容必须原封不动
        self.assertEqual(
            self.store.get_setup(self.project["id"], "owner-1")["version"], 0
        )

    def test_endpoint_without_if_match_still_works(self):
        """老客户端不带这个头：无条件保存（不能因为加了并发控制就把它弄坏）。"""
        request = projects_module.SimulationSetup(**_setup_payload())
        saved = asyncio.run(
            projects_module.put_project_setup(
                self.project["id"], request, user={"id": "owner-1"}
            )
        )
        self.assertEqual(saved.version, 1)


class SetupMigrationTest(unittest.TestCase):
    """老库（没有 setup 列）打开时自动补列，且旧数据不丢。"""

    def test_old_database_gains_the_setup_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"

            connection = sqlite3.connect(str(db_path))
            connection.executescript(
                """
                CREATE TABLE projects (
                    id              TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    description     TEXT NOT NULL DEFAULT '',
                    simulation_type TEXT NOT NULL DEFAULT 'General',
                    is_private      INTEGER NOT NULL DEFAULT 1,
                    owner_id        TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                ("legacy1", "老项目", "描述", "FEA", 1, "user-a",
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            connection.commit()
            connection.close()

            store = ProjectStore(db_path)

            self.assertIn("setup", store.columns())
            row = store.get_project("legacy1", "user-a")
            self.assertEqual(row["title"], "老项目", "迁移后旧数据必须还在")
            self.assertFalse(row["hasSetup"])
            self.assertEqual(
                store.get_setup("legacy1", "user-a"), {"setup": None, "savedAt": None, "version": 0}
            )
            # 迁移后新保存也应正常
            store.set_setup("legacy1", _setup_payload(), owner_id="user-a")
            self.assertTrue(store.get_project("legacy1", "user-a")["hasSetup"])

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "repeat.db"
            for _ in range(3):
                store = ProjectStore(db_path)
            store.set_setup(
                store.create(title="x", owner_id="u")["id"], {"version": 1},
                owner_id="u",
            )
            self.assertEqual(ProjectStore(db_path).count("u"), 1)


if __name__ == "__main__":
    unittest.main()
