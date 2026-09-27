"""
认证 API：注册 / 登录 / 登出 / 当前用户 + ``require_user`` 依赖。

为什么用"服务端会话令牌"而不是 JWT
----------------------------------
本项目选的是**不透明随机令牌 + SQLite 会话表**，理由有三条：

1. **可以吊销**。登出、改密、踢掉某个设备都是删一行。JWT 想做到同样的事还得
   再维护一张黑名单表——那时你已经有了"服务端状态"，却还背着 JWT 的全部复杂度。
2. **不需要自己实现密码学**。签发/校验 JWT 要在标准库上手工拼 HMAC、base64url
   和声明校验；"自己写 token 校验"是经典的高危动作（漏校验 ``alg``、漏校验
   ``exp``、时序比较……）。随机串 + 数据库查询没有这些问题。
3. **库里只存令牌的哈希**，数据库被读走也拿不到可直接使用的令牌。

代价是每个请求多一次索引查询——对 SQLite 可以忽略。

令牌怎么带
----------
``Authorization: Bearer <token>``。前端存在 ``localStorage`` 里。

> 取舍说明：``localStorage`` 无法被 ``HttpOnly`` 保护，因此一旦出现 XSS，
> 令牌可被读走。改用 ``HttpOnly`` Cookie 就得同时处理 CSRF（SameSite + token）。
> 对当前这个"本机/小范围部署的教学项目"来说，Bearer + localStorage 的复杂度
> 更低，且没有引入 CSRF 面。**这一点在文档里写清楚，不假装它更安全。**

错误码约定
----------
- 缺少或无效令牌 → **401**（并带 ``WWW-Authenticate: Bearer``）
- 用户名/口令不对 → **401**，且提示**不区分**"用户不存在"与"口令错误"
  （区分开就等于免费提供了用户名枚举接口）
- 已登录但不拥有目标资源 → **404**（见 `projects.py`，不泄露 id 是否存在）
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth_store import get_store as get_auth_store
from config import (
    ALLOW_REGISTRATION,
    AUTH_TOKEN_TTL_DAYS,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
)
from logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()

#: 用户名只允许字母、数字、下划线、连字符与点。放宽到"任意字符"会让同名混淆
#: （例如全角/半角、不可见字符）变得可能，而用户名是要给人看、要出现在 URL 里的。
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

Response = dict


def normalise_username(username: str) -> str:
    """
    用户名归一化：去首尾空白 + 转小写。

    只做这一处归一化（登录与注册都调用它），因此 ``Alice`` 与 ``alice``
    必然被当成同一个人——如果两处各写一套，就会出现"注册成 Alice 却能用 alice
    再注册一个"这种账号混淆。
    """
    return str(username or "").strip().lower()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., max_length=USERNAME_MAX_LENGTH)
    password: str = Field(..., max_length=PASSWORD_MAX_LENGTH)
    displayName: Optional[str] = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        normalised = normalise_username(value)
        if len(normalised) < USERNAME_MIN_LENGTH:
            raise ValueError(f"用户名至少 {USERNAME_MIN_LENGTH} 个字符")
        if not _USERNAME_PATTERN.match(normalised):
            raise ValueError("用户名只能包含字母、数字、下划线、连字符和点")
        return normalised

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        # 不 strip：前后空格是合法的口令字符，悄悄去掉会让用户"明明输对了却登不上"
        if len(value) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"口令至少 {PASSWORD_MIN_LENGTH} 个字符")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., max_length=USERNAME_MAX_LENGTH)
    password: str = Field(..., max_length=PASSWORD_MAX_LENGTH)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        return normalise_username(value)


class UserResponse(BaseModel):
    id: str
    username: str
    displayName: str
    createdAt: str


class AuthResponse(BaseModel):
    """登录/注册成功后的返回：用户信息 + 令牌。"""

    user: UserResponse
    token: str
    #: 令牌过期时间（ISO-8601 UTC 字符串）
    expiresAt: str


def _public_user(user: dict) -> UserResponse:
    """对外只暴露必要字段——**绝不包含 passwordHash**。"""
    return UserResponse(
        id=user["id"],
        username=user["username"],
        displayName=user["displayName"],
        createdAt=user["createdAt"],
    )


# --------------------------------------------------------------------- 依赖


async def require_user(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """
    FastAPI 依赖：把 ``Authorization: Bearer <token>`` 解析成用户记录。

    任何失败都是 401，并且带 ``WWW-Authenticate`` 头（这是 HTTP 语义要求，
    也方便前端区分"未登录"与"权限不足"）。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="需要登录：请在 Authorization 头里提供 Bearer 令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    user = get_auth_store().resolve_token(token)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def optional_user(
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict]:
    """已登录则返回用户，否则返回 ``None``（不报错）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return get_auth_store().resolve_token(authorization.split(" ", 1)[1].strip())


# --------------------------------------------------------------------- 端点


@router.post("/auth/register", response_model=AuthResponse, status_code=201)
async def register(request: RegisterRequest):
    """
    注册并直接登录（返回令牌，省一次往返）。

    注意这里**不做**"首个用户自动接管无主项目"：那会静默改变数据归属，
    本项目已经因此把开发者手工建的项目划给了 `verify` 的测试账号
    （见 `project_store.py` 的模块文档）。无主项目改为**可见 + 显式认领**
    （`POST /api/projects/{id}/claim`）。
    """
    if not ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=403, detail="本部署已关闭自助注册，请使用已有账号登录"
        )

    store = get_auth_store()
    try:
        user = store.create_user(
            request.username,
            request.password,
            display_name=request.displayName or request.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 刻意**不**做"首个用户自动接管无主项目"：那会静默改变数据归属，
    # 本项目已经因此把开发者手工建的项目划给了 verify 的测试账号。
    # 无主项目改为"可见 + 显式认领"（POST /api/projects/{id}/claim）。
    issued = store.issue_token(user["id"])
    return AuthResponse(user=_public_user(user), **issued)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """用用户名 + 口令换一个令牌。"""
    store = get_auth_store()
    user = store.authenticate(request.username, request.password)
    if user is None:
        # 不区分"用户不存在"与"口令错误"：区分开就等于免费提供用户名枚举
        raise HTTPException(status_code=401, detail="用户名或口令不正确")

    issued = store.issue_token(user["id"])
    return AuthResponse(user=_public_user(user), **issued)


@router.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    """
    登出：删除当前会话。

    没有有效令牌时也返回成功——登出应当**幂等**，客户端不该因为"令牌本来就
    过期了"而看到一个错误。
    """
    if authorization and authorization.lower().startswith("bearer "):
        get_auth_store().revoke_token(authorization.split(" ", 1)[1].strip())
    return {"loggedOut": True}


@router.get("/auth/me", response_model=UserResponse)
async def me(user: dict = Depends(require_user)):
    """返回当前登录用户。前端启动时用它校验本地令牌还有效。"""
    return _public_user(user)


@router.get("/auth/config")
async def auth_config():
    """
    认证相关的公开配置（无需登录）。

    前端据此决定"要不要显示注册入口"，而不是把可选值硬编码两份
    （与 `/api/project-metadata` 同一个理由）。
    """
    return {
        "allowRegistration": ALLOW_REGISTRATION,
        "tokenTtlDays": AUTH_TOKEN_TTL_DAYS,
        "usernameMinLength": USERNAME_MIN_LENGTH,
        "usernameMaxLength": USERNAME_MAX_LENGTH,
        "passwordMinLength": PASSWORD_MIN_LENGTH,
        "passwordMaxLength": PASSWORD_MAX_LENGTH,
    }
