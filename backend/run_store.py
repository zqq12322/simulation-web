"""
求解运行记录（run history）——"这个项目跑过哪些算例、结果是多少"。

为什么值得单独建表
-----------------
在此之前，一个项目只有**当前配置**，没有**历史**：求解结果留在浏览器内存里，
刷新就没；被共享的人（viewer）打开项目只看到配置，看不到属主算出来的任何数值。

而"跑过哪些算例、各自什么网格、结果多少"恰恰是工程上最常被问的问题——
对比不同网格/参数的结果是收敛检查的自然下一步。所以记下来。

只存**摘要**，不存完整场
------------------------
位移/应力场是逐节点的数组，一个中等模型的 JSON 就有几 MB。存进 SQLite 会让
数据库迅速膨胀，而"长期可维护"里很大一部分就是**数据不要无限长**。所以这里
只存标量摘要（最大应力、最大位移……）与网格信息；完整数据由导出功能带走
（CSV / VTK，见 docs/03 第二十一轮）。

每个项目的记录数有上限（`MAX_RUNS_PER_PROJECT`），超出后按时间裁掉最旧的。
不设上限的表是那种"两年后才被发现"的问题。

数值必须有限
-----------
摘要要序列化成 JSON 存库。`NaN` / `Infinity` 不是合法 JSON——Python 的
`json.dumps` 会写出 `NaN`（自己的 `json.loads` 读得回来），但浏览器的
`JSON.parse` **会直接抛错**，整个列表都打不开。所以这里显式拒绝非有限值，
让问题在**写入时**暴露，而不是在某个用户的浏览器里。

考察量复用 `convergence_study.extract_quantities`
-----------------------------------------------
"最大应力是哪个字段、模态取哪一阶"这套规则只能有一份实现。收敛检查已经写过
一次（含"模态要跳过刚体模态"这个坑），这里直接复用，不重写。
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from sqlite_store import SqliteStore

logger = get_logger(__name__)

#: 每个项目保留多少条记录（按时间倒序，超出裁掉最旧的）。
MAX_RUNS_PER_PROJECT = 50

#: 单条摘要的字节上限。正常只有几百字节；设上限是防止有人塞进来一个巨型 JSON。
RUN_SUMMARY_MAX_BYTES = 8 * 1024

#: 支持记录的分析类型（与 extract_quantities 一致）
RUN_ANALYSIS_TYPES = ("structural", "thermal", "modal")


#: 每种分析类型允许记录的考察量。
#:
#: 与 `convergence_study.extract_quantities` 返回的键**必须一致**——由
#: `tests/test_runs.py` 钉住。这里不能直接共用那个函数，因为记录运行时的
#: **数据不在后端**（见 `validate_run_summary` 的说明）；既然不能共用代码，
#: 至少让"两边漂移"立刻在测试里失败，而不是悄悄少记一个量。
RUN_QUANTITIES: Dict[str, tuple] = {
    "structural": ("max_stress", "max_displacement"),
    "thermal": ("max_heat_flux", "max_temperature"),
    "modal": ("first_elastic_frequency",),
}


def _finite(value: Any) -> Optional[float]:
    """取有限浮点数；非数字或非有限返回 None。"""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_run_summary(
    analysis_type: str,
    quantities: Any,
    mesh_size: Any = None,
    elements: Any = None,
    nodes: Any = None,
    warnings: Any = None,
) -> Dict[str, Any]:
    """
    校验并规范化一条运行摘要（前端提交的**只有数值**）。

    为什么不让前端把整个求解结果传上来、由后端提取摘要：位移/应力场是逐节点
    数组，中等模型的 JSON 就有几 MB，而求解任务刚刚才把它传给浏览器——
    为了记几个数再回传一遍是纯粹的浪费。所以前端只提交它已经拿到的标量。

    代价是"哪个字段是最大应力"这套规则在两侧各有一份。这里用两道闸门保证
    它们不漂移：

    1. `RUN_QUANTITIES` 显式声明每种分析类型允许的键，
       **多余或缺失的键一律拒绝**——拼错的字段不会被静默存下来（那会变成
       "历史记录里这一项一直是空的"）；
    2. `tests/test_runs.py` 断言这份声明与 `convergence_study.extract_quantities`
       的键集合完全一致。

    数值必须**有限**：`NaN` / `Infinity` 不是合法 JSON，存进去会让前端
    `JSON.parse` 抛错、整个历史列表都打不开。宁可在这里报 400。
    """
    if analysis_type not in RUN_ANALYSIS_TYPES:
        raise ValueError(f"analysis_type 只能是 {'/'.join(RUN_ANALYSIS_TYPES)}")
    if not isinstance(quantities, dict):
        raise ValueError("quantities 必须是对象")

    expected = set(RUN_QUANTITIES[analysis_type])
    provided = set(quantities)
    missing = expected - provided
    extra = provided - expected
    if missing:
        raise ValueError(f"{analysis_type} 缺少考察量：{', '.join(sorted(missing))}")
    if extra:
        raise ValueError(
            f"{analysis_type} 不接受这些考察量：{', '.join(sorted(extra))}"
            f"（允许：{', '.join(sorted(expected))}）"
        )

    cleaned: Dict[str, float] = {}
    for name in RUN_QUANTITIES[analysis_type]:
        number = _finite(quantities[name])
        if number is None:
            raise ValueError(f"考察量 {name} 不是有限数值（{quantities[name]!r}）")
        cleaned[name] = number

    mesh: Dict[str, Any] = {}
    if mesh_size is not None:
        number = _finite(mesh_size)
        if number is None or number <= 0:
            raise ValueError("网格尺寸必须是正的有限数值")
        mesh["meshSize"] = number
    for key, value in (("elements", elements), ("nodes", nodes)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} 必须是非负整数")
        mesh[key] = value

    warning_list = [str(item) for item in (warnings or []) if str(item).strip()]
    return {
        "quantities": cleaned,
        "mesh": mesh,
        # 只留前三条：目的是让人知道"这次有降级/忽略的东西"，不是存档警告全文
        "warnings": warning_list[:3],
        "warningCount": len(warning_list),
    }


def _serialise(summary: Dict[str, Any]) -> str:
    """摘要 -> JSON 文本，并检查体积上限。"""
    text = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > RUN_SUMMARY_MAX_BYTES:
        raise ValueError(
            f"摘要超过 {RUN_SUMMARY_MAX_BYTES} 字节上限，拒绝记录"
        )
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore(SqliteStore):
    """`project_runs` 表的读写。"""

    table = "project_runs"
    schema = """
    CREATE TABLE IF NOT EXISTS project_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        created_by TEXT,
        analysis_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        setup_signature TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_runs_project
        ON project_runs(project_id, created_at DESC);
    """

    #: 给**已有老库**补列。SQLite 没有 `ADD COLUMN IF NOT EXISTS`，基类会先查
    #: `PRAGMA table_info` 再加（幂等）。
    migrations = {"setup_signature": "TEXT"}

    def record(
        self,
        project_id: str,
        analysis_type: str,
        summary: Dict[str, Any],
        created_by: Optional[str] = None,
        run_id: Optional[str] = None,
        setup_signature: Optional[str] = None,
    ) -> Dict[str, Any]:
        """记一条运行；返回值与 `list_for_project` 的元素同形。"""
        if analysis_type not in RUN_ANALYSIS_TYPES:
            raise ValueError(f"analysis_type 只能是 {'/'.join(RUN_ANALYSIS_TYPES)}")
        identifier = run_id or uuid.uuid4().hex[:12]
        created_at = _now_iso()
        payload = _serialise(summary)

        with self._cursor() as connection:
            connection.execute(
                "INSERT INTO project_runs"
                " (id, project_id, created_by, analysis_type, summary,"
                "  setup_signature, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier, project_id, created_by, analysis_type, payload,
                    setup_signature, created_at,
                ),
            )
        # 裁掉最旧的：不设上限的表是那种"两年后才发现"的问题
        self.trim(project_id)
        logger.info("记录运行 %s（项目 %s，类型 %s）", identifier, project_id, analysis_type)
        return {
            "id": identifier,
            "project_id": project_id,
            "created_by": created_by,
            "analysis_type": analysis_type,
            "summary": summary,
            "setup_signature": setup_signature,
            "created_at": created_at,
        }

    def trim(self, project_id: str, keep: int = MAX_RUNS_PER_PROJECT) -> int:
        """只保留最新的 `keep` 条，返回删掉的条数。"""
        if keep < 1:
            raise ValueError("keep 必须至少为 1")
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT id FROM project_runs WHERE project_id = ?"
                " ORDER BY created_at DESC, rowid DESC",
                (project_id,),
            ).fetchall()
            excess = [row["id"] for row in rows[keep:]]
            for identifier in excess:
                connection.execute(
                    "DELETE FROM project_runs WHERE id = ?", (identifier,)
                )
        if excess:
            logger.info("项目 %s 的运行记录超过上限，裁掉 %d 条", project_id, len(excess))
        return len(excess)

    def list_for_project(self, project_id: str, limit: int = MAX_RUNS_PER_PROJECT) -> List[dict]:
        """该项目的运行记录，**最新的在前**。"""
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT * FROM project_runs WHERE project_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, int(limit)),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, run_id: str) -> Optional[dict]:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM project_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, run_id: str, project_id: Optional[str] = None) -> bool:
        """
        删一条记录。带 `project_id` 时会校验归属——否则知道 id 就能删别的项目的记录。
        """
        with self._cursor() as connection:
            if project_id is None:
                cursor = connection.execute(
                    "DELETE FROM project_runs WHERE id = ?", (run_id,)
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM project_runs WHERE id = ? AND project_id = ?",
                    (run_id, project_id),
                )
            return cursor.rowcount > 0

    def count_for_project(self, project_id: str) -> int:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM project_runs WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["total"]) if row else 0

    def delete_for_project(self, project_id: str) -> int:
        """删掉某个项目的全部记录（删除项目时调用，避免留下孤儿行）。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM project_runs WHERE project_id = ?", (project_id,)
            )
            return cursor.rowcount

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        try:
            summary = json.loads(row["summary"] or "{}")
        except (json.JSONDecodeError, TypeError):
            # 库里的 JSON 坏了不该让整个列表打不开：给一个空摘要并允许调用方
            # 通过 `summaryParseError` 看出问题（这正是"NaN 会让前端崩"那条
            # 教训的延伸——坏数据要在这一层被隔离）。
            logger.warning("运行记录 %s 的摘要无法解析", row["id"])
            summary = {}
            parse_error = True
        else:
            parse_error = False
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "created_by": row["created_by"],
            "analysis_type": row["analysis_type"],
            "summary": summary,
            "setup_signature": row["setup_signature"],
            "created_at": row["created_at"],
            "summary_parse_error": parse_error,
        }


# ---------------------------------------------------------------------------
# 跨运行对比："同一套配置下，结果随加密稳定了吗"
# ---------------------------------------------------------------------------

#: 少于这么多次运行就不做收敛判断（两级结果接近可能是收敛，也可能是两处都错
#: 得一样——这条规矩与收敛检查那轮一致）。
MIN_RUNS_FOR_ASSESSMENT = 3


def _mesh_measures(summary: Dict[str, Any]) -> tuple:
    """
    返回 ``(fineness, h)``：前者越大表示网格越**细**，后者是平均单元尺寸（估阶用）。

    为什么要专门抽一个函数、并且返回**两个**数：有两种可得的信息，而它们的
    单调方向**相反**——单元数越大越细，名义网格尺寸越大越粗。第一版直接拿
    "单元数"当排序键、又拿它算 ``N^(-1/3)``，结果粗细分序搞反、估阶直接失效
    （`generalized_order` 要求尺寸严格递减，拿到递增序列只能返回 None，
    表现为"无法判断"）。

    没有单元数时（老记录/手工填的）回退到名义尺寸，此时 ``fineness = 1/尺寸``
    保证两种来源的"粗细"含义一致。
    """
    mesh = summary.get("mesh") or {}
    elements = mesh.get("elements")
    if isinstance(elements, int) and not isinstance(elements, bool) and elements > 0:
        return float(elements), float(elements) ** (-1.0 / 3.0)
    mesh_size = mesh.get("meshSize")
    if (
        isinstance(mesh_size, (int, float))
        and not isinstance(mesh_size, bool)
        and mesh_size > 0
        and math.isfinite(float(mesh_size))
    ):
        size = float(mesh_size)
        return 1.0 / size, size
    return None, None


def assess_run_history(runs: List[dict]) -> List[Dict[str, Any]]:
    """
    把项目的运行记录按"可比较"的组整理，并对每组做一次收敛判定。

    **为什么必须先分组**：只有**配置相同、只有网格不同**的几次运行才能当成一条
    收敛序列。材料变了、边界条件变了、几何换了，数值的变化就跟网格没关系，
    把它们放在一起算"收敛阶"是编数字。所以按
    ``(分析类型, 配置签名)`` 分组，签名不同的组各自独立。

    没有签名的记录（本功能上线之前的旧数据）会被放在一个
    ``comparable=False`` 的组里：**照实显示、但拒绝判定**，并说明原因。
    对旧数据"猜它们配置相同"是不可接受的。

    组内还会**按网格去重**（同一个网格跑两次只留最新的一次）：重复的网格尺寸
    不是一次加密，留着会让密序不严格递减，判定直接失效。

    返回的每组包含原始数值与平均单元尺寸（都是**粗 → 细**）、判定结果与结论。
    这是**事后**对比，不是受控的加密实验——结论里会写明这一点。
    """
    from convergence import assess

    grouped: Dict[tuple, List[dict]] = {}
    order: List[tuple] = []
    for run in runs:
        signature = run.get("setup_signature")
        key = (run.get("analysis_type") or "", signature)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(run)

    groups: List[Dict[str, Any]] = []
    for key in order:
        analysis_type, signature = key
        members = grouped[key]
        quantities = RUN_QUANTITIES.get(analysis_type)
        if not quantities:
            continue
        quantity = quantities[0]          # 每种分析类型的"主考察量"排在第一位

        # 去重：同一个网格只留**最新**的一次（输入已按时间倒序）
        seen: Dict[float, dict] = {}
        for run in members:
            fineness, h = _mesh_measures(run.get("summary") or {})
            value = (run.get("summary") or {}).get("quantities", {}).get(quantity)
            if fineness is None or not isinstance(value, (int, float)):
                continue
            if fineness not in seen:
                seen[fineness] = {"run": run, "h": h, "value": float(value)}

        # **粗 → 细**：按"细度"升序
        entries = sorted(seen.values(), key=lambda item: item["h"], reverse=True)

        group: Dict[str, Any] = {
            "analysisType": analysis_type,
            "setupSignature": signature,
            "comparable": signature is not None,
            "runCount": len(members),
            "distinctMeshCount": len(entries),
            "quantity": quantity,
            "values": [item["value"] for item in entries],
            "sizes": [item["h"] for item in entries],
            "runIds": [item["run"]["id"] for item in entries],
            "assessment": None,
            "verdict": "",
        }

        if not group["comparable"]:
            group["verdict"] = (
                "这些运行没有记录配置签名（本功能上线前的旧记录），"
                "无法确认它们只有网格不同，因此**不做收敛判断**。"
                "重新跑一次求解即可带上签名。"
            )
            groups.append(group)
            continue

        if len(entries) < MIN_RUNS_FOR_ASSESSMENT:
            group["verdict"] = (
                f"同一套配置下只有 {len(entries)} 个不同的网格"
                f"（需要 {MIN_RUNS_FOR_ASSESSMENT} 个才能判断），暂不做收敛判断。"
                "改变网格尺寸多跑几次即可。"
            )
            groups.append(group)
            continue

        sizes = group["sizes"]
        assessment = assess(
            group["values"],
            expected_order=None,
            label=f"运行历史 · {quantity}",
            sizes=sizes,
        )
        group["assessment"] = assessment
        group["verdict"] = (
            "【事后对比，不是受控加密实验】" + str(assessment.get("verdict", ""))
        )
        groups.append(group)

    return groups
