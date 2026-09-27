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
- **查询一律要求显式给出 ``owner_id``**（见下）。

关于 ``owner_id``
-----------------
第一版**故意没有**这个字段：一个"存在但没人校验"的属主字段比没有更危险，
它会让人以为数据已经隔离了。现在真正接上登录了，才把它加上——
`SqliteStore.migrations` 就是为这种演进准备的（材料库已演示过一次）。

``owner_id IS NULL`` 表示**本轮之前创建的遗留项目**（那时还没有用户概念）。
对它的策略是刻意保守的：

- **可见**：已登录用户能在列表里看到它们（否则用户会以为自己的项目丢了）；
- **不可改、不可删**：修改与删除只允许属主本人；
- **可以显式认领**（`claim`）：由用户主动触发，一行 SQL 把它划给自己。

**为什么不用"第一个注册的用户自动接管"**：那是本轮真的踩到的坑。
`tools/tasks.py verify` 会注册固定的测试账号，一旦它是第一个用户，就会把开发者
手工建的项目静默划给自己——用户下次登录发现项目不见了。
**静默改变数据归属，比"看得见但要手点一下"危险得多。**

查询方法的 ``owner_id`` 是**必填参数**，并且**没有"返回全部"这个模式**：

- ``owner_id="<用户>"`` → 只看这个用户的；``include_unowned=True`` 时附上无主项目；
- ``owner_id=None``     → 只看**无主**项目。

如果把 ``owner_id=None`` 解释成"不过滤"，任何一处忘记传参的调用都会静默地把所有
用户的数据混在一起——而这正是"用户隔离"要防的事。宁可让调用方多写一个参数。
"""

from __future__ import annotations

import json
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
    owner_id        TEXT,
    setup           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

#: 后加的列：``owner_id``（接上登录时新增）、``setup``（仿真配置 JSON）。
#: 老库会自动补列，旧行为 NULL。
_MIGRATIONS = {
    "owner_id": "TEXT",
    "setup": "TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_project_id() -> str:
    """服务端生成的项目 ID（12 位十六进制，与 job id 风格一致）。"""
    return uuid.uuid4().hex[:12]


def parse_setup(raw: Optional[str]) -> Optional[dict]:
    """
    把存储里的配置 JSON 解析成 dict；**空值或坏数据都返回 ``None``**。

    这条规则必须只有一份实现：`get_setup`（详情）与 `_row_to_dict`（列表里的
    `hasSetup` 标记）都要用它。否则会出现"列表说已配置、打开却是空的"——
    一个很小但确实在骗人的不一致（本模块的测试就是这么发现的：
    `hasSetup` 原本只判断字符串非空，坏 JSON 也算"已配置"）。

    这里刻意**不打日志**（列表接口会逐行调用，会刷屏）；需要诊断的那条路径
    （`get_setup`）自己判断并记录。
    """
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class ProjectStore(SqliteStore):
    """项目的持久化存储。所有查询都按 ``owner_id`` 限定范围，见模块文档。"""

    table = "projects"
    schema = _SCHEMA
    migrations = _MIGRATIONS

    # ------------------------------------------------------------- 读
    def list_projects(
        self, owner_id: Optional[str], include_unowned: bool = False
    ) -> List[dict]:
        """
        列出**某个属主**的项目，最新建的在前。

        ``owner_id=None`` 表示"只看无主项目"（**不是**"所有项目"，见模块文档）。
        ``include_unowned=True`` 时把无主项目也带进来（列表里它们 ``ownerId`` 为
        ``None``，前端据此显示"未归属"）。
        """
        if include_unowned and owner_id is not None:
            sql = ("SELECT * FROM projects WHERE owner_id IS ? OR owner_id IS NULL"
                   " ORDER BY rowid DESC")
        else:
            sql = "SELECT * FROM projects WHERE owner_id IS ? ORDER BY rowid DESC"

        with self._cursor() as connection:
            rows = connection.execute(sql, (owner_id,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_project(
        self,
        project_id: str,
        owner_id: Optional[str] = None,
        include_unowned: bool = False,
    ) -> Optional[dict]:
        """
        按 ID 取项目，并限定属主。

        不属于该属主时返回 ``None``（调用方转成 404）——**不区分"不存在"与
        "不是你的"**，否则可以通过 id 探测别人有哪些项目。
        """
        sql = "SELECT * FROM projects WHERE id = ? AND owner_id IS ?"
        if include_unowned and owner_id is not None:
            sql = ("SELECT * FROM projects WHERE id = ?"
                   " AND (owner_id IS ? OR owner_id IS NULL)")
        with self._cursor() as connection:
            row = connection.execute(sql, (project_id, owner_id)).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self, owner_id: Optional[str] = None) -> int:
        with self._cursor() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM projects WHERE owner_id IS ?", (owner_id,)
            ).fetchone()[0])

    def count_all(self) -> int:
        """全库项目数（只用于日志/诊断，不用于任何面向用户的查询）。"""
        with self._cursor() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM projects"
            ).fetchone()[0])

    # ------------------------------------------------------------- 写
    def create(
        self,
        title: str,
        owner_id: Optional[str] = None,
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
                         is_private, owner_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["id"],
                        payload["title"],
                        payload["description"],
                        payload["simulationType"],
                        1 if payload["isPrivate"] else 0,
                        owner_id,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:  # 并发/注入 ID 冲突时的兜底
                raise ValueError(f"项目 ID 已存在：{payload['id']}") from exc

        logger.info("已创建项目：%s（%s，属主 %s）",
                    payload["id"], payload["title"], owner_id or "无主")
        return self.get_project(payload["id"], owner_id) or payload

    #: ``update`` 允许改的字段 → 数据库列名
    _UPDATABLE = {
        "title": "title",
        "description": "description",
        "simulationType": "simulation_type",
        "isPrivate": "is_private",
    }

    def update(
        self, project_id: str, owner_id: Optional[str] = None, **fields
    ) -> Optional[dict]:
        """
        局部更新；**不属于该属主或不存在**都返回 ``None``。

        传入未在 ``_UPDATABLE`` 里的字段会抛 ``ValueError``——宁可大声失败，
        也不要"看起来更新了其实什么都没改"。（``ownerId`` 也在这里被挡住：
        项目不能在用户之间被"过户"。）
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
        values.extend([project_id, owner_id])

        with self._cursor() as connection:
            cursor = connection.execute(
                f"UPDATE projects SET {', '.join(assignments)}"
                " WHERE id = ? AND owner_id IS ?",
                values,
            )
            if cursor.rowcount == 0:
                return None

        logger.info("已更新项目：%s（字段 %s）", project_id, ", ".join(fields))
        return self.get_project(project_id, owner_id)

    def delete(self, project_id: str, owner_id: Optional[str] = None) -> bool:
        """删除一个项目；返回是否真的删掉了（不属于该属主时返回 False）。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM projects WHERE id = ? AND owner_id IS ?",
                (project_id, owner_id),
            )
            removed = cursor.rowcount > 0
        if removed:
            logger.info("已删除项目：%s", project_id)
        return removed

    def claim(self, project_id: str, owner_id: str) -> Optional[dict]:
        """
        认领一个**无主**项目；返回认领后的记录。

        - 项目不存在 → ``None``
        - 项目已属于别人 → ``None``（调用方转 404，不泄露它属于谁）
        - 项目已经是自己的 → 幂等成功，返回原记录（重复点击不该报错）

        ``WHERE owner_id IS NULL`` 是这里的关键：**不能**写成无条件 UPDATE，
        否则这个方法就变成了"任意项目过户"。

        为什么要有这个方法（而不是自动接管）：见模块文档。自动接管曾被
        `tools/tasks.py verify` 的测试账号触发，把开发者手工建的项目静默划走了。
        """
        with self._cursor() as connection:
            existing = connection.execute(
                "SELECT owner_id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if existing is None:
                return None

            current_owner = existing["owner_id"]
            if current_owner is not None and current_owner != owner_id:
                return None
            if current_owner is None:
                connection.execute(
                    "UPDATE projects SET owner_id = ?, updated_at = ?"
                    " WHERE id = ? AND owner_id IS NULL",
                    (owner_id, _now(), project_id),
                )
                logger.info("项目 %s 已被用户 %s 认领", project_id, owner_id)

        return self.get_project(project_id, owner_id)

    # ------------------------------------------------------------- 仿真配置
    def get_setup(
        self, project_id: str, owner_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        取项目的仿真配置：``{"setup": dict|None, "savedAt": str|None}``。

        **项目不存在或不属于该属主时返回 ``None``**（调用方转 404）。
        注意区分两种"空"：
        - 项目不存在 → ``None``（404）
        - 项目存在但没保存过配置 → ``{"setup": None, ...}``（200）

        混在一起会让前端把"还没配过"当成"项目没了"。
        """
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT setup, updated_at FROM projects WHERE id = ? AND owner_id IS ?",
                (project_id, owner_id),
            ).fetchone()
        if row is None:
            return None

        raw = row["setup"]
        parsed = parse_setup(raw)
        if raw and parsed is None:
            # 库里的内容坏了：如实按"空"处理并留日志，而不是让端点 500
            # （500 的话用户完全不知道发生了什么，也不知道配置已经丢了）
            logger.warning("项目 %s 的仿真配置无法解析，已按空处理", project_id)
        if parsed is None:
            return {"setup": None, "savedAt": None}
        return {"setup": parsed, "savedAt": row["updated_at"]}

    def set_setup(
        self, project_id: str, setup: dict, owner_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        覆盖保存仿真配置；项目不存在或不属于该属主时返回 ``None``。

        **整份覆盖**而不是局部合并：配置是一份文档，局部合并表达不了
        "删掉一个边界条件"这类操作（前端发来的本来就是完整状态）。
        """
        payload = json.dumps(setup, ensure_ascii=False, separators=(",", ":"))
        with self._cursor() as connection:
            cursor = connection.execute(
                "UPDATE projects SET setup = ?, updated_at = ?"
                " WHERE id = ? AND owner_id IS ?",
                (payload, _now(), project_id, owner_id),
            )
            if cursor.rowcount == 0:
                return None
        logger.info("已保存项目 %s 的仿真配置（%d 字节）", project_id, len(payload))
        return self.get_setup(project_id, owner_id)

    def clear_setup(self, project_id: str, owner_id: Optional[str] = None) -> bool:
        """清空仿真配置（保留项目本身）。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "UPDATE projects SET setup = NULL, updated_at = ?"
                " WHERE id = ? AND owner_id IS ?",
                (_now(), project_id, owner_id),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------- 工具
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        keys = row.keys()
        setup = row["setup"] if "setup" in keys else None
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "simulationType": row["simulation_type"],
            "isPrivate": bool(row["is_private"]),
            "ownerId": row["owner_id"],
            # 列表里**不返回完整配置**（可能很大）：只给一个"配过没有"的标记，
            # 仪表盘据此显示"已配置 / 空项目"。完整内容走 /setup 子资源。
            # 用 `parse_setup` 而不是 `bool(setup)`：坏 JSON 必须与详情页
            # 保持一致（都算"空"），否则会出现"列表说已配置、打开却是空的"。
            "hasSetup": parse_setup(setup) is not None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


_default_store: Optional[ProjectStore] = None


def get_store() -> ProjectStore:
    """进程内共享的默认存储（惰性创建）。"""
    global _default_store
    if _default_store is None:
        _default_store = ProjectStore()
        logger.info("项目数据库：%s（共 %d 个项目）",
                    _default_store.db_path, _default_store.count_all())
    return _default_store
