"""
认证持久化：用户与会话（SQLite，标准库）。

设计要点
--------
1. **会话是不可预测的随机串，但库里只存它的哈希**（SHA-256）。
   数据库被读走时，攻击者拿不到可以直接使用的登录令牌——这和"口令只存哈希"
   是同一个道理。令牌本身只在签发那一次返回给客户端。
2. **会话存在服务端**（而不是自包含的 JWT），因此可以**吊销**：登出、改密、
   管理员踢人都是删一行。JWT 想要同样的效果还得再维护一张黑名单，反而更复杂。
   代价是每次请求多一次索引查询——对 SQLite 而言可以忽略。
3. **过期时间写进表里**，查询时用 SQL 过滤，而不是"取出来再比较"：
   过期会话不会因为忘记判断而被放行。
4. **首次注册时接管遗留项目**：本轮之前创建的项目 ``owner_id`` 是 NULL
   （那时候还没有用户概念）。第一个注册的用户会把它们接管过来，
   否则用户会以为自己的项目丢了。只做一次，且只做无主项目。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from config import AUTH_TOKEN_TTL_DAYS
from logging_config import get_logger
from passwords import hash_password, needs_rehash, verify_password
from sqlite_store import SqliteStore

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""

#: 令牌随机部分的字节数（``token_urlsafe`` 之后约 43 个字符）
TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    """
    令牌 → 库里存的哈希。

    这里用**普通 SHA-256** 而不是 scrypt：令牌是 32 字节的高熵随机串，
    不存在"被猜到"的问题，不需要慢哈希；而每个请求都要查一次会话，
    用 scrypt 会让每个 API 调用多花几十毫秒。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore(SqliteStore):
    """用户与会话的持久化存储。"""

    table = "users"
    schema = _SCHEMA

    # ------------------------------------------------------------- 用户
    def count_users(self) -> int:
        with self._cursor() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0])

    def get_user(self, user_id: str) -> Optional[dict]:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._cursor() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def create_user(
        self, username: str, password: str, display_name: str = ""
    ) -> dict:
        """
        创建用户。用户名重复时抛 ``ValueError``。

        ``username`` 的大小写归一化由调用方（`auth.py`）完成——存储层只负责
        "原样唯一"，避免两处各写一套归一化规则。
        """
        user_id = secrets.token_hex(8)
        created_at = _iso(_now())

        with self._cursor() as connection:
            try:
                connection.execute(
                    "INSERT INTO users (id, username, password_hash, display_name,"
                    " created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, hash_password(password),
                     display_name or username, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"用户名已被占用：{username}") from exc

        logger.info("已创建用户：%s（%s）", username, user_id)
        return self.get_user(user_id)  # type: ignore[return-value]

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        """
        校验口令，成功返回用户记录，失败返回 ``None``。

        用户不存在时也会**做一次哈希校验**（用固定的假哈希），让"用户不存在"与
        "口令错误"的耗时接近，避免通过响应时间枚举用户名。
        """
        user = self.get_user_by_username(username)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user["passwordHash"]):
            return None

        # 登录成功是升级哈希参数的最好时机（用户已经证明了自己知道口令）
        if needs_rehash(user["passwordHash"]):
            self._set_password(user["id"], password)
            user = self.get_user(user["id"])
        return user

    def _set_password(self, user_id: str, password: str) -> None:
        with self._cursor() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
        logger.info("已按当前参数重算用户口令哈希：%s", user_id)

    # ------------------------------------------------------------- 会话
    def issue_token(self, user_id: str, ttl_days: Optional[int] = None) -> dict:
        """
        签发一个新的登录令牌。

        返回 ``{"token": ..., "expiresAt": ...}``；**token 只在这里出现一次**，
        库里只留它的哈希。
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        created = _now()
        expires = created + timedelta(days=ttl_days or AUTH_TOKEN_TTL_DAYS)

        with self._cursor() as connection:
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (hash_token(token), user_id, _iso(created), _iso(expires)),
            )

        logger.info("已为用户 %s 签发令牌（%s 过期）", user_id, _iso(expires))
        return {"token": token, "expiresAt": _iso(expires)}

    def resolve_token(self, token: str) -> Optional[dict]:
        """
        令牌 → 用户记录。不存在、已过期都返回 ``None``。

        **过期判断放在 SQL 里**（``expires_at > ?``），而不是取出来再比较：
        这样不会因为某处忘记判断而放行一个过期会话。
        """
        if not token:
            return None

        with self._cursor() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM sessions AS s
                JOIN users AS u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (hash_token(token), _iso(_now())),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def revoke_token(self, token: str) -> bool:
        """登出：删除该会话。返回是否真的删掉了一行。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),)
            )
            removed = cursor.rowcount > 0
        if removed:
            logger.info("已吊销令牌")
        return removed

    def revoke_user_tokens(self, user_id: str) -> int:
        """吊销某用户的全部会话（改密后应当调用）。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE user_id = ?", (user_id,)
            )
            removed = cursor.rowcount
        logger.info("已吊销用户 %s 的 %d 个会话", user_id, removed)
        return int(removed)

    def purge_expired_sessions(self) -> int:
        """清理过期会话（可在启动时调用；不做也不影响正确性）。"""
        with self._cursor() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_iso(_now()),)
            )
            return int(cursor.rowcount)

    def list_sessions(self, user_id: str) -> List[dict]:
        with self._cursor() as connection:
            rows = connection.execute(
                "SELECT token_hash, created_at, expires_at FROM sessions"
                " WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "createdAt": row["created_at"],
                "expiresAt": row["expires_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------- 工具
    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "createdAt": row["created_at"],
            # 只在内部使用；`auth.py` 对外一律不返回
            "passwordHash": row["password_hash"],
        }


#: 用户不存在时用来"陪跑"的假哈希：让两条失败路径耗时接近。
#: 明文是什么不重要（没人知道也不该被匹配到），但必须是合法的 scrypt 串。
_DUMMY_HASH = (
    "scrypt$16384$8$1$"
    + "00" * 16
    + "$"
    + "00" * 32
)

_default_store: Optional[AuthStore] = None


def get_store() -> AuthStore:
    """进程内共享的默认存储（惰性创建）。"""
    global _default_store
    if _default_store is None:
        _default_store = AuthStore()
        purged = _default_store.purge_expired_sessions()
        logger.info(
            "认证数据库：%s（%d 个用户，清理了 %d 个过期会话）",
            _default_store.db_path, _default_store.count_users(), purged,
        )
    return _default_store
