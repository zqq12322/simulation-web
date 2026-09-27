"""
材料库持久化（SQLite，标准库，不引入新依赖）。

背景
----
材料库原本是一个内存里的 Python 列表：用户辛苦建的自定义材料**一重启就没了**，
多人共用一台机器时也互相覆盖。这既不像一个能长期使用的工具，也让"协作"无从谈起
（每个人都要重新录一遍材料）。

设计
----
- **内置材料始终来自代码**（`materials.MATERIALS_DB`），保证任何时候都可用、
  且不会因为数据库损坏而丢失；
- **自定义材料写入 SQLite**，重启后仍在；
- 数据库路径默认 `backend/data/simcloud.db`（已被 .gitignore 忽略），
  可用环境变量 `SIMCLOUD_DB` 覆盖——测试就靠它使用临时库，不污染开发数据。

之所以选 SQLite 而不是 JSON 文件：并发写入更安全（多人/多进程），
且有主键约束能天然防止 ID 重复。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from config import DB_PATH, ensure_data_dir
from logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_materials (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    density        REAL NOT NULL,
    youngs_modulus REAL NOT NULL,
    poissons_ratio REAL NOT NULL,
    color          TEXT NOT NULL,
    type           TEXT NOT NULL,
    thermal_conductivity REAL,
    description    TEXT,
    created_at     TEXT NOT NULL
);
"""

#: 后加的列 → 列定义。SQLite 没有 "ADD COLUMN IF NOT EXISTS"，
#: 所以启动时按 PRAGMA table_info 判断再补，老数据库也能平滑升级。
_MIGRATIONS = {
    "thermal_conductivity": "REAL",
}


class MaterialStore:
    """自定义材料的持久化存储。"""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        if self.db_path.parent and str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------- 内部
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _cursor(self):
        """
        打开连接 → 事务 → **关闭连接**。

        注意：``with sqlite3.connect(...) as conn`` 是个常见的坑——连接对象作为
        上下文管理器只负责提交/回滚事务，**不会关闭连接**。那样每次调用都会泄漏
        一个句柄，在 Windows 上会一直锁住数据库文件（删除/移动都会失败）。
        """
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._cursor() as connection:
            connection.executescript(_SCHEMA)
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """给老数据库补上后加的列（幂等）。"""
        with self._cursor() as connection:
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(custom_materials)")
            }
            for column, definition in _MIGRATIONS.items():
                if column in existing:
                    continue
                connection.execute(
                    f"ALTER TABLE custom_materials ADD COLUMN {column} {definition}"
                )
                logger.info("材料库迁移：新增列 %s %s", column, definition)

    # ------------------------------------------------------------- 读
    def list_custom(self) -> List[dict]:
        """返回全部自定义材料（按创建时间排序）。"""
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT * FROM custom_materials ORDER BY created_at, id"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_custom(self, material_id: str) -> Optional[dict]:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM custom_materials WHERE id = ?", (material_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self) -> int:
        with self._cursor() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM custom_materials"
            ).fetchone()[0])

    # ------------------------------------------------------------- 写
    def add(self, material) -> dict:
        """
        写入一个自定义材料。

        ``material`` 可以是 ``materials.Material`` 模型或等价 dict。
        ID 重复时抛 ``ValueError``（由调用方转成 HTTP 400）。
        """
        payload = self._normalise(material)

        if self.get_custom(payload["id"]) is not None:
            raise ValueError(f"材料 ID 已存在：{payload['id']}")

        with self._cursor() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO custom_materials
                        (id, name, density, youngs_modulus, poissons_ratio,
                         color, type, thermal_conductivity, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["id"],
                        payload["name"],
                        payload["density"],
                        payload["youngsModulus"],
                        payload["poissonsRatio"],
                        payload["color"],
                        payload["type"],
                        payload.get("thermalConductivity"),
                        payload.get("description"),
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
            except sqlite3.IntegrityError as exc:  # 并发写入时的兜底
                raise ValueError(f"材料 ID 已存在：{payload['id']}") from exc

        logger.info("已保存自定义材料：%s（%s）", payload["id"], payload["name"])
        return payload

    def delete(self, material_id: str) -> bool:
        """删除一个自定义材料；返回是否真的删掉了。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM custom_materials WHERE id = ?", (material_id,)
            )
            removed = cursor.rowcount > 0
        if removed:
            logger.info("已删除自定义材料：%s", material_id)
        return removed

    # ------------------------------------------------------------- 工具
    @staticmethod
    def _normalise(material) -> dict:
        """把 Material 模型 / dict 统一成存档用的 dict（字段名与 API 一致）。"""
        if isinstance(material, dict):
            data = dict(material)
        else:
            data = material.model_dump()

        required = ("id", "name", "density", "youngsModulus", "poissonsRatio", "color", "type")
        missing = [key for key in required if data.get(key) is None]
        if missing:
            raise ValueError(f"材料缺少必填字段：{', '.join(missing)}")

        return {
            "id": str(data["id"]),
            "name": str(data["name"]),
            "density": float(data["density"]),
            "youngsModulus": float(data["youngsModulus"]),
            "poissonsRatio": float(data["poissonsRatio"]),
            "color": str(data["color"]),
            "type": str(data["type"]),
            "thermalConductivity": (
                float(data["thermalConductivity"])
                if data.get("thermalConductivity") is not None
                else None
            ),
            "description": data.get("description"),
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {
            "id": row["id"],
            "name": row["name"],
            "density": row["density"],
            "youngsModulus": row["youngs_modulus"],
            "poissonsRatio": row["poissons_ratio"],
            "color": row["color"],
            "type": row["type"],
            "thermalConductivity": (
                row["thermal_conductivity"] if "thermal_conductivity" in keys else None
            ),
            "description": row["description"],
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"MaterialStore({self.db_path})"


_default_store: Optional[MaterialStore] = None


def get_store() -> MaterialStore:
    """进程内共享的默认存储（惰性创建）。"""
    global _default_store
    if _default_store is None:
        ensure_data_dir()
        _default_store = MaterialStore()
        logger.info("材料库数据库：%s（已存 %d 个自定义材料）",
                    _default_store.db_path, _default_store.count())
    return _default_store
