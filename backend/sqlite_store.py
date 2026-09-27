"""
SQLite 存储的公共基类：连接 / 事务 / **关闭** + 幂等加列迁移。

为什么单独成模块
----------------
项目里有多个持久化实体（自定义材料、项目……），它们要处理的 SQLite 细节**完全一样**，
而且其中有一条是踩过坑的：

``with sqlite3.connect(...) as conn`` 作为上下文管理器**只提交/回滚事务，不关闭连接**。
每次调用都会泄漏一个句柄，在 Windows 上会一直锁住数据库文件（删除/移动都失败）。
本项目曾因此在测试里出现 14 个 tearDown 失败。

这类规则写两遍就一定会漂移，所以抽到这里：谁要持久化谁继承，改一次两边生效。
这与 `fe_utils.py` 抽公共 FE 基础设施是同一个理由。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Optional

from config import DB_PATH, ensure_data_dir
from logging_config import get_logger

logger = get_logger(__name__)


class SqliteStore:
    """
    一个"一张表 + 可选加列迁移"的 SQLite 存储。

    子类只需要声明三件事：

    - ``table``：表名（迁移时用 ``PRAGMA table_info`` 查列）；
    - ``schema``：``CREATE TABLE IF NOT EXISTS ...`` 建表语句（必须幂等）；
    - ``migrations``：``{列名: 列定义}``，用于给**别人机器上已有的老库**补列。
      SQLite 没有 ``ADD COLUMN IF NOT EXISTS``，所以要先查再加。

    数据库文件默认 ``backend/data/simcloud.db``（已 gitignore），
    可用环境变量 ``SIMCLOUD_DB`` 覆盖——测试就靠它用临时库，不污染开发数据。
    多个 Store 可以共用同一个文件（各建各的表）。
    """

    #: 子类必须覆盖
    table: str = ""
    schema: str = ""
    #: 后加的列 → 列定义。默认空：新表不需要迁移。
    migrations: Mapping[str, str] = {}

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if not self.table:
            raise TypeError(f"{type(self).__name__} 必须声明 table")
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        if self.db_path.parent and str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------- 连接
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Connection]:
        """
        打开连接 → 事务 → **关闭连接**。

        见模块文档：``with sqlite3.connect(...)`` 不会关闭连接，会泄漏句柄并锁住
        数据库文件。这里显式 ``close()``。
        """
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # ------------------------------------------------------------- 建表 / 迁移
    def _init_schema(self) -> None:
        with self._cursor() as connection:
            connection.executescript(self.schema)
        self._apply_migrations()

    def columns(self, table: Optional[str] = None) -> set:
        """列出某张表的列名（默认本 Store 的表）。"""
        target = table or self.table
        with self._cursor() as connection:
            return {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({target})")
            }

    def _apply_migrations(self) -> None:
        """
        给老数据库补上后加的列（幂等）。

        ``self.columns()`` 自己开一次连接，因此这里先查完再开写连接，
        避免同一个方法里嵌套两个连接。
        """
        if not self.migrations:
            return
        existing = self.columns()
        pending = {
            column: definition
            for column, definition in self.migrations.items()
            if column not in existing
        }
        if not pending:
            return

        with self._cursor() as connection:
            for column, definition in pending.items():
                connection.execute(
                    f"ALTER TABLE {self.table} ADD COLUMN {column} {definition}"
                )
                logger.info("%s 迁移：新增列 %s %s", self.table, column, definition)

    # ------------------------------------------------------------- 工具
    @staticmethod
    def _ensure_data_dir() -> None:
        ensure_data_dir()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"{type(self).__name__}({self.db_path})"
