"""
项目持久化（SQLite，标准库，不引入新依赖）。

背景
----
在此之前**根本没有"项目"这个后端实体**：仪表盘上的项目列表是前端硬编码的一个
数组（`{ id: '1', title: 'Aerodynamic Wing v3', ... }`），新建项目只存在内存里，
刷新就没了，重启更是全丢。这意味着两件事无从谈起：

1. **长期使用**——每次打开都要重新建项目、重新导入几何、重新配边界条件；
2. **协作**——没有一个可以被"共享/引用"的对象。你说"我那个悬臂梁项目"，
   对方拿不到任何东西，因为它在你的浏览器内存里。

这一轮先做**持久化本身**（谁拥有、能不能共享是下一步的事，见 `docs/01` 阶段 3）。

设计取舍
--------
- **ID 由服务端生成**（``uuid4().hex[:12]``，与 `jobs.py` 的 job id 一致）。
  前端曾用 ``Date.now().toString()``：那个值既可预测又取决于客户端时钟，
  而且客户端能自选 ID 就意味着能覆盖别人的记录。
- **排序按 ``rowid``**，不按 ``created_at``：时间戳只精确到秒，
  同一秒内建的两个项目会并列，"最新的在最前"就变成不确定的顺序。
- ``owner_id`` / 鉴权**故意还没加**：一个"存在但没人校验"的属主字段比没有更危险
  ——它会让人以为数据已经隔离了。等真正接上登录时再加列，
  ``SqliteStore.migrations`` 就是为这种演进准备的（材料库已经演示过一次）。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from logging_config import get_logger
from sqlite_store import SqliteStore

logger = get_logger(__name__)

#: 与前端 `types.ts` 的 `Project.simulationType` 保持一致
SIMULATION_TYPES = ("CFD", "FEA", "Thermal", "General")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    simulation_type TEXT NOT NULL DEFAULT 'General',
    is_private      INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_project_id() -> str:
    """服务端生成的项目 ID（12 位十六进制，与 job id 风格一致）。"""
    return uuid.uuid4().hex[:12]


class ProjectStore(SqliteStore):
    """项目的持久化存储。"""

    table = "projects"
    schema = _SCHEMA

    # ------------------------------------------------------------- 读
    def list_projects(self) -> List[dict]:
        """
        返回全部项目，**最新建的在前**。

        按 ``rowid`` 而不是 ``created_at`` 排序：时间戳只到秒，
        同一秒内创建的项目用时间戳排序结果不确定（测试里就会时好时坏）。
        """
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY rowid DESC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self) -> int:
        with self._cursor() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0])

    # ------------------------------------------------------------- 写
    def create(
        self,
        title: str,
        description: str = "",
        simulation_type: str = "General",
        is_private: bool = True,
        project_id: Optional[str] = None,
    ) -> dict:
        """
        新建项目并返回存档后的记录。

        ``project_id`` 仅供测试注入固定 ID 用；正常调用一律由服务端生成。
        """
        payload = {
            "id": project_id or new_project_id(),
            "title": title,
            "description": description or "",
            "simulationType": simulation_type,
            "isPrivate": bool(is_private),
        }
        timestamp = _now()

        with self._cursor() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO projects
                        (id, title, description, simulation_type,
                         is_private, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["id"],
                        payload["title"],
                        payload["description"],
                        payload["simulationType"],
                        1 if payload["isPrivate"] else 0,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:  # 并发/注入 ID 冲突时的兜底
                raise ValueError(f"项目 ID 已存在：{payload['id']}") from exc

        logger.info("已创建项目：%s（%s）", payload["id"], payload["title"])
        return self.get_project(payload["id"]) or payload

    #: ``update`` 允许改的字段 → 数据库列名
    _UPDATABLE = {
        "title": "title",
        "description": "description",
        "simulationType": "simulation_type",
        "isPrivate": "is_private",
    }

    def update(self, project_id: str, **fields) -> Optional[dict]:
        """
        局部更新；项目不存在返回 ``None``。

        传入未在 ``_UPDATABLE`` 里的字段会抛 ``ValueError``——宁可大声失败，
        也不要"看起来更新了其实什么都没改"。
        """
        unknown = sorted(set(fields) - set(self._UPDATABLE))
        if unknown:
            raise ValueError(f"不支持更新的字段：{', '.join(unknown)}")
        if not fields:
            raise ValueError("没有提供任何要更新的字段")

        assignments = []
        values = []
        for key, value in fields.items():
            assignments.append(f"{self._UPDATABLE[key]} = ?")
            if key == "isPrivate":
                value = 1 if value else 0
            values.append(value)
        assignments.append("updated_at = ?")
        values.append(_now())
        values.append(project_id)

        with self._cursor() as connection:
            cursor = connection.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None

        logger.info("已更新项目：%s（字段 %s）", project_id, ", ".join(fields))
        return self.get_project(project_id)

    def delete(self, project_id: str) -> bool:
        """删除一个项目；返回是否真的删掉了。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM projects WHERE id = ?", (project_id,)
            )
            removed = cursor.rowcount > 0
        if removed:
            logger.info("已删除项目：%s", project_id)
        return removed

    # ------------------------------------------------------------- 工具
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "simulationType": row["simulation_type"],
            "isPrivate": bool(row["is_private"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


_default_store: Optional[ProjectStore] = None


def get_store() -> ProjectStore:
    """进程内共享的默认存储（惰性创建）。"""
    global _default_store
    if _default_store is None:
        _default_store = ProjectStore()
        logger.info("项目数据库：%s（已有 %d 个项目）",
                    _default_store.db_path, _default_store.count())
    return _default_store
