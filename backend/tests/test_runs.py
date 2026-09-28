"""
求解记录的测试。

四层，各回答一个不同的问题：

1. `RunQuantityDeclarationTest`——**声明与实现会不会漂移**：记录用的考察量集合
   必须与收敛检查的 `extract_quantities` 完全一致。两边不能共用代码（记录时数据
   不在后端），所以至少让漂移立刻失败；
2. `ValidateRunSummaryTest`——**校验闸门**：拼错的字段、非有限值必须被拒绝，
   而不是静默存下来（那会变成"历史里这一项一直是空的"，没人知道为什么）；
3. `RunStoreTest`——存储行为：倒序、裁剪上限、坏 JSON 隔离、随项目一起删；
4. `RunsApiTest`——权限：**每一个写端点都同时断言"有权限成功"与"没权限失败"**
   （这是共享那一轮定下的规矩：权限最容易出的问题是"某处忘了判断"）。

另外一条贯穿始终的：摘要里**不允许出现非有限值**。`NaN` 不是合法 JSON，
存进去会让前端的 `JSON.parse` 抛错、整个历史列表打不开——所以要求在写入时
就报 400，而不是等某个用户的浏览器白屏。
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

import projects as projects_module
import runs as runs_module
from convergence_study import extract_quantities
from project_store import ProjectStore
from run_store import (
    MAX_RUNS_PER_PROJECT,
    RUN_ANALYSIS_TYPES,
    RUN_QUANTITIES,
    RunStore,
    validate_run_summary,
)
from runs import RunCreate

GOOD_STRUCTURAL = {"max_stress": 6.5e7, "max_displacement": 2.2e-6}
GOOD_THERMAL = {"max_heat_flux": 5.0e5, "max_temperature": 373.15}
GOOD_MODAL = {"first_elastic_frequency": 1234.5}


class _Structural:
    max_stress = 6.5e7
    max_displacement = 2.2e-6
    warnings: list = []


class _Thermal:
    max_heat_flux = 5.0e5
    max_temperature = 373.15
    warnings: list = []


class _Modal:
    frequencies = [0.0] * 6 + [1234.5, 2345.6]
    rigid_body_modes = 6
    warnings: list = []


_SYNTHETIC = {
    "structural": _Structural(),
    "thermal": _Thermal(),
    "modal": _Modal(),
}
_GOOD = {
    "structural": GOOD_STRUCTURAL,
    "thermal": GOOD_THERMAL,
    "modal": GOOD_MODAL,
}


class RunQuantityDeclarationTest(unittest.TestCase):
    """
    记录用的考察量集合必须与收敛检查的一致。

    两者不能共用代码（记录运行时数据不在后端，见 run_store 的说明），所以这里
    用一条测试代替"共用"：谁加了一个考察量而没同步另一处，这条就会红。
    """

    def test_declaration_matches_convergence_quantities(self):
        for analysis_type in RUN_ANALYSIS_TYPES:
            with self.subTest(analysis_type=analysis_type):
                extracted = extract_quantities(analysis_type, _SYNTHETIC[analysis_type])
                self.assertEqual(
                    set(RUN_QUANTITIES[analysis_type]),
                    set(extracted),
                    f"{analysis_type}：RUN_QUANTITIES 与 extract_quantities 不一致",
                )

    def test_every_good_payload_passes_validation(self):
        """上面那条用的样本本身必须是合法载荷（否则它验的是别的东西）。"""
        for analysis_type in RUN_ANALYSIS_TYPES:
            with self.subTest(analysis_type=analysis_type):
                summary = validate_run_summary(analysis_type, _GOOD[analysis_type])
                self.assertEqual(
                    set(summary["quantities"]), set(RUN_QUANTITIES[analysis_type])
                )


class ValidateRunSummaryTest(unittest.TestCase):
    def test_accepts_a_complete_payload(self):
        summary = validate_run_summary(
            "structural", GOOD_STRUCTURAL,
            mesh_size=1.25, elements=2735, nodes=832, warnings=["忽略了一个约束"],
        )
        self.assertEqual(summary["quantities"]["max_stress"], 6.5e7)
        self.assertEqual(summary["mesh"], {"meshSize": 1.25, "elements": 2735, "nodes": 832})
        self.assertEqual(summary["warningCount"], 1)
        self.assertEqual(summary["warnings"], ["忽略了一个约束"])

    def test_missing_quantity_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_run_summary("structural", {"max_stress": 1.0})
        self.assertIn("缺少考察量", str(ctx.exception))
        self.assertIn("max_displacement", str(ctx.exception))

    def test_unknown_quantity_is_rejected(self):
        """
        拼错的字段必须被拒绝。

        静默接受会变成"历史记录里这一项一直是空的"，而没人知道为什么——
        这正是"多余字段直接 422"那条规矩在数据层的版本。
        """
        with self.assertRaises(ValueError) as ctx:
            validate_run_summary(
                "structural", {"max_stress": 1.0, "max_displacement": 2.0, "max_strss": 3.0}
            )
        self.assertIn("不接受", str(ctx.exception))
        self.assertIn("max_strss", str(ctx.exception))

    def test_cross_type_quantity_is_rejected(self):
        """热分析的载荷不能拿来记结构分析（键名相同也不行）。"""
        with self.assertRaises(ValueError):
            validate_run_summary("thermal", GOOD_STRUCTURAL)

    def test_non_finite_is_rejected(self):
        """
        `NaN` / `Infinity` 不是合法 JSON。

        Python 的 `json.dumps` 会写出 `NaN`，自己的 `json.loads` 也读得回来，
        但浏览器的 `JSON.parse` **会直接抛错**——整个历史列表都打不开。
        所以必须在写入时就拒绝。
        """
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError) as ctx:
                    validate_run_summary(
                        "structural", {"max_stress": bad, "max_displacement": 1.0}
                    )
                self.assertIn("有限数值", str(ctx.exception))

    def test_non_numeric_is_rejected(self):
        for bad in ("6.5e7", None, True, [1.0], {"v": 1}):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    validate_run_summary(
                        "structural", {"max_stress": bad, "max_displacement": 1.0}
                    )

    def test_bool_is_not_a_number(self):
        """`True` 在 Python 里是 int——但不该被当成数值记下来。"""
        with self.assertRaises(ValueError):
            validate_run_summary(
                "structural", {"max_stress": True, "max_displacement": 1.0}
            )

    def test_unknown_analysis_type_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_run_summary("cfd", GOOD_STRUCTURAL)
        self.assertIn("analysis_type", str(ctx.exception))

    def test_quantities_must_be_an_object(self):
        with self.assertRaises(ValueError):
            validate_run_summary("structural", ["max_stress", 1.0])

    def test_mesh_fields_are_validated(self):
        with self.assertRaises(ValueError):
            validate_run_summary("structural", GOOD_STRUCTURAL, mesh_size=0)
        with self.assertRaises(ValueError):
            validate_run_summary("structural", GOOD_STRUCTURAL, mesh_size=float("nan"))
        with self.assertRaises(ValueError):
            validate_run_summary("structural", GOOD_STRUCTURAL, elements=-1)
        with self.assertRaises(ValueError):
            validate_run_summary("structural", GOOD_STRUCTURAL, elements=2.5)
        with self.assertRaises(ValueError):
            validate_run_summary("structural", GOOD_STRUCTURAL, nodes=True)

    def test_mesh_fields_are_optional(self):
        summary = validate_run_summary("structural", GOOD_STRUCTURAL)
        self.assertEqual(summary["mesh"], {})

    def test_warnings_are_capped_but_counted(self):
        """只留前三条（给人知道"这次有降级"），但总数要如实报出来。"""
        summary = validate_run_summary(
            "structural", GOOD_STRUCTURAL,
            warnings=["a", "b", "c", "d", "e"],
        )
        self.assertEqual(summary["warnings"], ["a", "b", "c"])
        self.assertEqual(summary["warningCount"], 5)

    def test_blank_warnings_are_dropped(self):
        summary = validate_run_summary(
            "structural", GOOD_STRUCTURAL, warnings=["", "  ", "真的警告"]
        )
        self.assertEqual(summary["warnings"], ["真的警告"])
        self.assertEqual(summary["warningCount"], 1)


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name) / "runs.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _record(self, project_id="p1", analysis_type="structural", **kwargs):
        summary = validate_run_summary(analysis_type, _GOOD[analysis_type], **kwargs)
        return self.store.record(project_id, analysis_type, summary, created_by="alice")

    def test_record_and_get(self):
        record = self._record(mesh_size=1.25, elements=2735, nodes=832)
        self.assertEqual(record["project_id"], "p1")
        self.assertEqual(record["analysis_type"], "structural")
        fetched = self.store.get(record["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["summary"]["quantities"]["max_stress"], 6.5e7)
        self.assertEqual(fetched["summary"]["mesh"]["elements"], 2735)
        self.assertFalse(fetched["summary_parse_error"])

    def test_get_unknown_is_none(self):
        self.assertIsNone(self.store.get("nope"))

    def test_list_is_newest_first(self):
        identifiers = [self._record()["id"] for _ in range(3)]
        listed = [item["id"] for item in self.store.list_for_project("p1")]
        self.assertEqual(listed, list(reversed(identifiers)))

    def test_list_is_scoped_to_the_project(self):
        self._record(project_id="p1")
        self._record(project_id="p2")
        self.assertEqual(len(self.store.list_for_project("p1")), 1)
        self.assertEqual(len(self.store.list_for_project("p2")), 1)
        self.assertEqual(self.store.count_for_project("p1"), 1)

    def test_trim_keeps_the_newest(self):
        """
        超出上限时裁掉**最旧的**，保留最新的。

        不设上限的表是那种"两年后才被发现"的问题——本项目已经在
        "任务表在进程内、重启即丢"上写过一次类似的注记。
        """
        for _ in range(MAX_RUNS_PER_PROJECT + 5):
            self._record()
        remaining = self.store.list_for_project("p1", limit=10_000)
        self.assertEqual(len(remaining), MAX_RUNS_PER_PROJECT)
        self.assertEqual(self.store.count_for_project("p1"), MAX_RUNS_PER_PROJECT)

    def test_trim_rejects_a_silly_keep(self):
        with self.assertRaises(ValueError):
            self.store.trim("p1", keep=0)

    def test_delete_is_scoped_by_project(self):
        """带 project_id 时不能删别的项目的记录（否则知道 id 就能跨项目删）。"""
        record = self._record(project_id="p1")
        self.assertFalse(self.store.delete(record["id"], project_id="p2"))
        self.assertIsNotNone(self.store.get(record["id"]))
        self.assertTrue(self.store.delete(record["id"], project_id="p1"))
        self.assertIsNone(self.store.get(record["id"]))

    def test_delete_for_project_removes_all(self):
        for _ in range(3):
            self._record(project_id="p1")
        self._record(project_id="p2")
        self.assertEqual(self.store.delete_for_project("p1"), 3)
        self.assertEqual(self.store.count_for_project("p1"), 0)
        self.assertEqual(self.store.count_for_project("p2"), 1)

    def test_unknown_analysis_type_cannot_be_recorded(self):
        with self.assertRaises(ValueError):
            self.store.record("p1", "cfd", {"quantities": {}})

    def test_corrupt_summary_is_isolated(self):
        """
        库里那条 JSON 坏了（例如手工改过库）时，只让这一条降级。

        整份列表打不开是最坏的结果——用户会以为"记录全丢了"。
        """
        record = self._record()
        with self.store._cursor() as connection:      # noqa: SLF001 - 测试里故意写坏数据
            connection.execute(
                "UPDATE project_runs SET summary = ? WHERE id = ?",
                ("{ not json", record["id"]),
            )
        listed = self.store.list_for_project("p1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["summary"], {})
        self.assertTrue(listed[0]["summary_parse_error"])

    def test_summary_is_stored_as_compact_json(self):
        """库里存的是紧凑 JSON（不是 Python repr），以便任何语言都能读。"""
        record = self._record()
        with self.store._cursor() as connection:      # noqa: SLF001 - 测试直接看库
            row = connection.execute(
                "SELECT summary FROM project_runs WHERE id = ?", (record["id"],)
            ).fetchone()
        parsed = json.loads(row["summary"])
        self.assertEqual(parsed["quantities"]["max_stress"], 6.5e7)
        self.assertNotIn(" ", row["summary"])          # 紧凑：无多余空格

    def test_oversized_summary_is_rejected(self):
        from run_store import RUN_SUMMARY_MAX_BYTES

        summary = validate_run_summary("structural", GOOD_STRUCTURAL)
        summary["warnings"] = ["x" * (RUN_SUMMARY_MAX_BYTES + 10)]
        with self.assertRaises(ValueError) as ctx:
            self.store.record("p1", "structural", summary)
        self.assertIn("上限", str(ctx.exception))

    def test_indices_and_table_exist(self):
        """建表语句必须幂等（重复构造同一个库不报错）。"""
        again = RunStore(Path(self._tmp.name) / "runs.db")
        self.assertEqual(again.count_for_project("p1"), 0)
        self.assertIn("project_id", again.columns())


class RunsApiTest(unittest.TestCase):
    """端点层：注入临时库，直接调用端点函数（不启动服务器）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.project_store = ProjectStore(root / "projects.db")
        self.run_store = RunStore(root / "runs.db")

        # `from project_store import get_store` 是**在导入时绑定函数对象**的，
        # 所以每个模块各自持有一份引用：只替换 projects_module 的那份，
        # runs_module 仍然会去开真实库（表现为"项目不存在 → 404"，
        # 排查起来很容易误以为是权限逻辑写错了）。逐个替换。
        self._orig = (
            projects_module.get_store,
            projects_module.get_run_store,
            runs_module.get_store,
            runs_module.get_run_store,
        )
        projects_module.get_store = lambda: self.project_store
        projects_module.get_run_store = lambda: self.run_store
        runs_module.get_store = lambda: self.project_store
        runs_module.get_run_store = lambda: self.run_store

        self.alice = {"id": "alice"}
        self.bob = {"id": "bob"}
        self.carol = {"id": "carol"}
        self.project = self.project_store.create(title="算例", owner_id="alice")

    def tearDown(self):
        (
            projects_module.get_store,
            projects_module.get_run_store,
            runs_module.get_store,
            runs_module.get_run_store,
        ) = self._orig
        self._tmp.cleanup()

    # ------------------------------------------------------------- 工具
    def _create(self, user, project_id=None, payload=None):
        request = RunCreate(**(payload or {
            "analysisType": "structural",
            "quantities": dict(GOOD_STRUCTURAL),
            "meshSize": 1.25,
            "elements": 2735,
            "nodes": 832,
        }))
        return asyncio.run(
            runs_module.create_project_run(project_id or self.project["id"], request, user)
        )

    def _list(self, user, project_id=None):
        return asyncio.run(
            runs_module.list_project_runs(project_id or self.project["id"], user)
        )

    def _delete(self, user, run_id, project_id=None):
        return asyncio.run(
            runs_module.delete_project_run(project_id or self.project["id"], run_id, user)
        )

    # ------------------------------------------------------------- 属主
    def test_owner_can_record_and_list(self):
        created = self._create(self.alice)
        self.assertEqual(created.analysisType, "structural")
        self.assertEqual(created.projectId, self.project["id"])
        self.assertEqual(created.createdBy, "alice")
        self.assertEqual(created.summary["quantities"]["max_stress"], 6.5e7)
        self.assertTrue(created.createdAt)          # ISO 字符串

        listed = self._list(self.alice)
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.limit, MAX_RUNS_PER_PROJECT)
        self.assertEqual([item.id for item in listed.runs], [created.id])

    def test_empty_history_is_not_an_error(self):
        """还没跑过 = 空列表，不是 404（项目本身存在）。"""
        listed = self._list(self.alice)
        self.assertEqual(listed.runs, [])
        self.assertEqual(listed.total, 0)

    def test_owner_can_delete(self):
        created = self._create(self.alice)
        self.assertIsNone(self._delete(self.alice, created.id))
        self.assertEqual(self._list(self.alice).total, 0)

    def test_delete_unknown_run_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self._delete(self.alice, "nope")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_cannot_cross_projects(self):
        """
        拿别的项目的 id 来删 → 404。

        否则知道一个 run id 就能删掉任意项目里的记录（id 是短随机串，
        但它不该是唯一的凭据）。
        """
        other = self.project_store.create(title="另一个", owner_id="alice")
        created = self._create(self.alice)
        with self.assertRaises(HTTPException) as ctx:
            self._delete(self.alice, created.id, project_id=other["id"])
        self.assertEqual(ctx.exception.status_code, 404)
        # 原记录还在
        self.assertEqual(self._list(self.alice).total, 1)

    # ------------------------------------------------------------- 共享
    def test_viewer_can_read_but_not_write(self):
        """
        只读者能看记录（这正是"共享"的意义：知道属主算过什么、什么网格），
        但**不能记也不能删**——记录属于项目内容。
        """
        created = self._create(self.alice)
        self.project_store.share(self.project["id"], "carol", "viewer")

        self.assertEqual(self._list(self.carol).total, 1)
        with self.assertRaises(HTTPException) as ctx:
            self._create(self.carol)
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            self._delete(self.carol, created.id)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_editor_can_record(self):
        self.project_store.share(self.project["id"], "bob", "editor")
        created = self._create(self.bob)
        self.assertEqual(created.createdBy, "bob")
        # 属主看得到协作者记的那条
        self.assertEqual(self._list(self.alice).total, 1)

    def test_stranger_gets_404_everywhere(self):
        """
        与项目没有关系的人：读也 404、写也 404。

        读用 404 是为了**不泄露项目是否存在**；写同样 404（他连看都看不到，
        没有"403 更诚实"的前提）。
        """
        created = self._create(self.alice)
        for action in (
            lambda: self._list(self.bob),
            lambda: self._create(self.bob),
            lambda: self._delete(self.bob, created.id),
        ):
            with self.assertRaises(HTTPException) as ctx:
                action()
            self.assertEqual(ctx.exception.status_code, 404)

    def test_unowned_project_is_readable_but_not_writable(self):
        """无主项目：可见（读得到历史）但不可写——必须先认领。"""
        orphan = self.project_store.create(title="无主的", owner_id=None)
        with self.assertRaises(HTTPException) as ctx:
            self._create(self.alice, project_id=orphan["id"])
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("认领", ctx.exception.detail)
        self.assertEqual(self._list(self.alice, project_id=orphan["id"]).total, 0)

    # ------------------------------------------------------------- 载荷
    def test_unknown_field_is_422(self):
        with self.assertRaises(ValidationError):
            RunCreate(analysisType="structural", quantities=dict(GOOD_STRUCTURAL),
                      meshSizee=1.0)

    def test_unknown_analysis_type_is_400(self):
        request = RunCreate(analysisType="cfd", quantities=dict(GOOD_STRUCTURAL))
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                runs_module.create_project_run(self.project["id"], request, self.alice)
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_typo_in_quantities_is_400_not_silently_stored(self):
        request = RunCreate(
            analysisType="structural",
            quantities={"max_stress": 1.0, "max_displacement": 2.0, "typo": 3.0},
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                runs_module.create_project_run(self.project["id"], request, self.alice)
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._list(self.alice).total, 0)

    def test_non_finite_is_400(self):
        request = RunCreate(
            analysisType="structural",
            quantities={"max_stress": float("nan"), "max_displacement": 1.0},
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                runs_module.create_project_run(self.project["id"], request, self.alice)
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_all_three_analysis_types_round_trip(self):
        for analysis_type in RUN_ANALYSIS_TYPES:
            with self.subTest(analysis_type=analysis_type):
                payload = {
                    "analysisType": analysis_type,
                    "quantities": dict(_GOOD[analysis_type]),
                }
                created = self._create(self.alice, payload=payload)
                self.assertEqual(created.analysisType, analysis_type)
                listed = self._list(self.alice)
                match = [item for item in listed.runs if item.id == created.id]
                self.assertEqual(len(match), 1)
                self.assertEqual(
                    set(match[0].summary["quantities"]),
                    set(RUN_QUANTITIES[analysis_type]),
                )

    # ------------------------------------------------------------- 级联
    def test_deleting_the_project_removes_its_runs(self):
        """
        删项目要一并清掉记录。

        否则会留下**再也无人能访问**的孤儿行（记录端点第一步就查项目权限，
        项目没了永远是 404），只会一直占空间。
        """
        self._create(self.alice)
        other = self.project_store.create(title="保留", owner_id="alice")
        self._create(self.alice, project_id=other["id"])

        asyncio.run(projects_module.delete_project(self.project["id"], self.alice))

        self.assertEqual(self.run_store.count_for_project(self.project["id"]), 0)
        self.assertEqual(self.run_store.count_for_project(other["id"]), 1)


if __name__ == "__main__":
    unittest.main()
