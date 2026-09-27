"""
项目持久化测试。

回归点：在这之前**后端根本没有"项目"这个实体**——仪表盘上是前端硬编码的一个
数组，新建的项目刷新就丢、重启更是全丢，也无法被引用或共享（见 `project_store.py`
的模块文档）。这里用**临时数据库**验证真实落盘行为，不污染开发数据。

覆盖四层，都是"不看代码就想不到会错"的地方：

1. **持久化本身**：换一个 store 实例（等价于进程重启）数据仍在；
2. **排序**：``created_at`` 只精确到秒，同一秒内建的项目必须仍有确定的"最新在前"
   ——所以按 ``rowid`` 排。这条如果写错，测试会时好时坏（典型的 flaky）。
3. **两库共存**：项目与材料库默认**共用同一个 SQLite 文件**（``SIMCLOUD_DB``）。
   各自建表、互不干扰，必须验证。
4. **HTTP 契约**：201/404/400/422 各自的触发条件，以及"ID 由服务端生成、
   客户端不能自选"（``extra="forbid"``）。
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import project_store as project_store_module
import projects as projects_module
from material_store import MaterialStore
from project_store import ProjectStore, new_project_id


class ProjectStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "projects.db"
        self.store = ProjectStore(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_store_is_empty(self):
        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual(self.store.count(), 0)

    def test_create_then_read_back(self):
        created = self.store.create(
            title="悬臂梁模态分析",
            description="10×10×100 钢梁，一端固支",
            simulation_type="FEA",
            is_private=True,
        )
        self.assertTrue(created["id"], "服务端必须生成 ID")
        self.assertEqual(created["title"], "悬臂梁模态分析")

        fetched = self.store.get_project(created["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["simulationType"], "FEA")
        self.assertIs(fetched["isPrivate"], True)
        self.assertEqual(fetched["createdAt"], fetched["updatedAt"])

    def test_persists_across_store_instances(self):
        """核心断言：换一个 ProjectStore 实例（模拟进程重启）数据仍在。"""
        created = self.store.create(title="重启也要在")

        reopened = ProjectStore(self.db_path)
        self.assertEqual(reopened.count(), 1)
        self.assertEqual(reopened.get_project(created["id"])["title"], "重启也要在")

    def test_ids_are_unique_and_server_generated(self):
        first = self.store.create(title="A")
        second = self.store.create(title="B")
        self.assertNotEqual(first["id"], second["id"])
        # 12 位十六进制，与 jobs.py 的 job id 风格一致
        self.assertRegex(first["id"], r"^[0-9a-f]{12}$")

    def test_newest_first_ordering_is_deterministic(self):
        """
        同一秒内连续创建 5 个项目，顺序必须严格是"最新在前"。

        按 ``created_at`` 排会全部并列（时间戳只到秒），顺序不确定 —— 这条测试
        就是用来钉住"按 rowid 排"这个决定的。
        """
        created = [self.store.create(title=f"项目 {index}") for index in range(5)]
        listed = self.store.list_projects()
        self.assertEqual(
            [item["id"] for item in listed],
            [item["id"] for item in reversed(created)],
        )

    def test_update_changes_fields_and_bumps_updated_at(self):
        created = self.store.create(title="旧名字", description="旧描述")
        updated = self.store.update(
            created["id"], title="新名字", isPrivate=False
        )
        self.assertEqual(updated["title"], "新名字")
        self.assertEqual(updated["description"], "旧描述", "未提供的字段不应被改动")
        self.assertIs(updated["isPrivate"], False)
        # created_at 不能因为更新而改变；updated_at 只会变新或持平
        self.assertEqual(updated["createdAt"], created["createdAt"])
        self.assertGreaterEqual(updated["updatedAt"], created["updatedAt"])

    def test_update_missing_project_returns_none(self):
        self.assertIsNone(self.store.update("does-not-exist", title="x"))

    def test_update_rejects_unknown_field(self):
        created = self.store.create(title="A")
        with self.assertRaises(ValueError):
            self.store.update(created["id"], owner_id="someone")

    def test_update_without_fields_is_rejected(self):
        created = self.store.create(title="A")
        with self.assertRaises(ValueError):
            self.store.update(created["id"])

    def test_delete_removes_only_target(self):
        keep = self.store.create(title="保留")
        drop = self.store.create(title="删掉")
        self.assertTrue(self.store.delete(drop["id"]))
        self.assertIsNone(self.store.get_project(drop["id"]))
        self.assertIsNotNone(self.store.get_project(keep["id"]))
        self.assertFalse(self.store.delete(drop["id"]), "重复删除应返回 False")

    def test_unicode_and_empty_description_round_trip(self):
        created = self.store.create(title="带孔平板 应力集中", description="")
        fetched = self.store.get_project(created["id"])
        self.assertEqual(fetched["title"], "带孔平板 应力集中")
        self.assertEqual(fetched["description"], "")

    def test_schema_creation_is_idempotent(self):
        """反复打开不应重复建表或报错。"""
        created = self.store.create(title="x")
        for _ in range(3):
            ProjectStore(self.db_path)
        self.assertEqual(ProjectStore(self.db_path).count(), 1)
        self.assertEqual(
            ProjectStore(self.db_path).get_project(created["id"])["title"], "x"
        )


class SharedDatabaseTest(unittest.TestCase):
    """
    项目库与材料库默认共用同一个 SQLite 文件（都由 ``config.DB_PATH`` 决定）。

    两个 Store 各建各的表，必须互不干扰——这是"多个存储共用一个文件"最容易
    出错的地方（表名冲突、迁移串台、误删别人的表）。
    """

    def test_projects_and_materials_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "shared.db"

            materials = MaterialStore(db_path)
            projects = ProjectStore(db_path)

            materials.add({
                "id": "custom_x", "name": "测试材料", "density": 1000.0,
                "youngsModulus": 1e9, "poissonsRatio": 0.3,
                "color": "#FFFFFF", "type": "custom",
            })
            created = projects.create(title="共存检查")

            # 重新打开（模拟重启）后两者都还在
            reopened_materials = MaterialStore(db_path)
            reopened_projects = ProjectStore(db_path)
            self.assertEqual(reopened_materials.count(), 1)
            self.assertEqual(reopened_projects.count(), 1)
            self.assertEqual(
                reopened_projects.get_project(created["id"])["title"], "共存检查"
            )

            # 删项目不应影响材料
            reopened_projects.delete(created["id"])
            self.assertEqual(reopened_projects.count(), 0)
            self.assertEqual(reopened_materials.count(), 1)

            # 两张表确实都在同一个文件里
            connection = sqlite3.connect(str(db_path))
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            connection.close()
            self.assertIn("projects", tables)
            self.assertIn("custom_materials", tables)


class ProjectsApiTest(unittest.TestCase):
    """端点层：注入临时库，验证 HTTP 语义与错误码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self._tmp.name) / "api.db")
        self._original = project_store_module.get_store
        project_store_module.get_store = lambda: self.store
        projects_module.get_store = lambda: self.store

    def tearDown(self):
        project_store_module.get_store = self._original
        projects_module.get_store = self._original
        self._tmp.cleanup()

    # ------------------------------------------------------------- 工具
    def _create(self, **overrides):
        payload = {"title": "新项目", "simulationType": "FEA"}
        payload.update(overrides)
        request = projects_module.ProjectCreate(**payload)
        return asyncio.run(projects_module.create_project(request))

    def _list(self):
        return asyncio.run(projects_module.list_projects())

    # ------------------------------------------------------------- 用例
    def test_create_returns_generated_id_and_timestamps(self):
        created = self._create()
        self.assertTrue(created.id)
        self.assertEqual(created.title, "新项目")
        self.assertEqual(created.simulationType, "FEA")
        self.assertIs(created.isPrivate, True, "默认应为私有")
        self.assertTrue(created.createdAt.endswith("+00:00"), created.createdAt)

    def test_list_is_newest_first(self):
        first = self._create(title="第一个")
        second = self._create(title="第二个")
        self.assertEqual([item.id for item in self._list()], [second.id, first.id])

    def test_get_unknown_id_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.get_project("nope"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_patch_updates_only_given_fields(self):
        created = self._create(description="原始描述")
        updated = asyncio.run(projects_module.update_project(
            created.id, projects_module.ProjectUpdate(title="改名了")
        ))
        self.assertEqual(updated.title, "改名了")
        self.assertEqual(updated.description, "原始描述")

    def test_patch_without_fields_returns_400(self):
        created = self._create()
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.update_project(
                created.id, projects_module.ProjectUpdate()
            ))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_patch_unknown_id_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.update_project(
                "nope", projects_module.ProjectUpdate(title="x")
            ))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_then_get_returns_404(self):
        created = self._create()
        result = asyncio.run(projects_module.delete_project(created.id))
        self.assertEqual(result, {"deleted": True, "id": created.id})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(projects_module.delete_project(created.id))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_blank_title_is_rejected(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(title=repr(blank)):
                with self.assertRaises(ValidationError):
                    projects_module.ProjectCreate(title=blank)

    def test_control_characters_are_stripped_from_title(self):
        request = projects_module.ProjectCreate(title="  悬臂\x00梁\n分析  ")
        self.assertEqual(request.title, "悬臂 梁 分析")

    def test_client_cannot_choose_id(self):
        """
        请求体里传 ``id`` 必须 422，而不是"被静默忽略"。

        若允许客户端自选主键，他就能覆盖别人的记录；若静默忽略，
        用户会以为按自己的 ID 存下来了。
        """
        with self.assertRaises(ValidationError):
            projects_module.ProjectCreate(id="my-own-id", title="x")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            projects_module.ProjectCreate(title="x", owner="someone")

    def test_invalid_simulation_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            projects_module.ProjectCreate(title="x", simulationType="CFD2")

    def test_title_length_limit(self):
        with self.assertRaises(ValidationError):
            projects_module.ProjectCreate(title="x" * 201)
        self.assertEqual(
            len(projects_module.ProjectCreate(title="x" * 200).title), 200
        )

    def test_metadata_lists_simulation_types(self):
        metadata = asyncio.run(projects_module.project_metadata())
        self.assertEqual(
            metadata["simulationTypes"], ["CFD", "FEA", "Thermal", "General"]
        )
        self.assertEqual(metadata["titleMaxLength"], 200)


class ProjectIdTest(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = {new_project_id() for _ in range(500)}
        self.assertEqual(len(ids), 500)


if __name__ == "__main__":
    unittest.main()
