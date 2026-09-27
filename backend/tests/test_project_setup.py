"""
项目仿真配置持久化测试。

回归点：在此之前"项目"只是一个名字 + 描述——几何、材料、边界条件、网格与求解
设置**全在浏览器内存里**，关掉页面就没了。于是"项目"这个概念的承诺（下次接着做）
实际上是空的：重新打开项目是一个空白工作台。

这组测试覆盖四层：
1. `SetupStoreTest`——存储：往返、覆盖、清空、**属主隔离**、坏 JSON 的兜底；
2. `SetupValidationTest`——校验策略是**分级**的（后端要消费的字段从严，
   纯界面设置从宽但有界），每一条都要有明确的理由；
3. `SetupApiTest`——端点语义：404（项目不属于你）与 200+null（还没配过）必须分开；
4. `SetupMigrationTest`——老库自动补 `setup` 列且旧数据不丢。
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
from project_store import ProjectStore


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
            {"setup": None, "savedAt": None},
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
            {"setup": None, "savedAt": None},
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
                store.get_setup("legacy1", "user-a"), {"setup": None, "savedAt": None}
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
