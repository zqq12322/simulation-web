"""
项目管理 API。

在这之前，项目只存在于前端内存里（`App.tsx` 里一个硬编码数组）：
新建的项目刷新就丢，重启更是全丢，也无法被引用/共享。
这个路由把"项目"变成一个真正持久化的后端实体。

接口约定
--------
- **ID 由服务端生成**，请求体里不接受 ``id``（``extra="forbid"`` 会直接 422）：
  客户端能自选主键就意味着能覆盖别人的记录。
- 时间戳一律 ISO-8601 UTC 字符串（``createdAt`` / ``updatedAt``）。
  **前端必须用 ``new Date(值)`` 解析**——它是个字符串，不是 ``Date`` 对象。
- 删除不存在的项目返回 404（而不是 204）：告诉调用方"其实没有这个东西"，
  比让他以为删除成功更安全。
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth import require_user
from logging_config import get_logger
from project_store import SIMULATION_TYPES, get_store

logger = get_logger(__name__)
router = APIRouter()

#: 控制字符（含 \t \n）在标题/描述里没有意义，却会让日志与界面难以阅读
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

TITLE_MAX = 200
DESCRIPTION_MAX = 2000

SimulationType = Literal["CFD", "FEA", "Thermal", "General"]


def _clean_text(value: str, field: str) -> str:
    """去掉首尾空白与控制字符；返回清洗后的文本。"""
    cleaned = _CONTROL_CHARS.sub(" ", str(value)).strip()
    if not cleaned:
        raise ValueError(f"{field} 不能为空")
    return cleaned


class ProjectCreate(BaseModel):
    """新建项目的请求体。``extra="forbid"``：多余字段直接 422。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=TITLE_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    simulationType: SimulationType = "General"
    isPrivate: bool = True

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_text(value, "项目名称")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _CONTROL_CHARS.sub(" ", str(value or "")).strip()


class ProjectUpdate(BaseModel):
    """
    局部更新。只允许改这四个字段，且**至少要给一个**。

    ``title=None`` 与"没传 title"是两件事：这里用 ``None`` 表示"不改"，
    因此也不允许把标题设成 ``null``（要清空就传空串——但那会被判为空标题）。
    """

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=TITLE_MAX)
    description: Optional[str] = Field(default=None, max_length=DESCRIPTION_MAX)
    simulationType: Optional[SimulationType] = None
    isPrivate: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _clean_text(value, "项目名称")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _CONTROL_CHARS.sub(" ", str(value)).strip()


class ProjectResponse(BaseModel):
    id: str
    title: str
    description: str
    simulationType: SimulationType
    isPrivate: bool
    #: 属主用户 id。当前实现里你只会看到自己的项目，因此它总是等于你本人；
    #: 保留在响应里是为了让"归属"这件事显式可见（也便于将来做共享）。
    ownerId: Optional[str] = None
    #: ISO-8601 UTC 字符串；前端用 new Date(...) 解析
    createdAt: str
    updatedAt: str


#: 所有项目端点都必须登录。**按属主过滤**（而不是"先查出来再判断"）：
#: 后者一旦某处漏判就会把别人的数据返回出去。
OwnedUser = Depends(require_user)


@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects(user: dict = OwnedUser):
    """
    列出**当前用户**的项目，最新建的在前。

    同时带上**无主项目**（``ownerId`` 为 ``null``，前端显示"未归属"）：它们是接上
    登录之前创建的数据，对已登录用户可见（否则用户会以为项目丢了），
    但**不可改**，需要显式调用 ``POST /api/projects/{id}/claim`` 认领。
    """
    return [
        ProjectResponse(**item)
        for item in get_store().list_projects(user["id"], include_unowned=True)
    ]


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectCreate, user: dict = OwnedUser):
    """新建项目（归当前用户所有）。ID 与时间戳由服务端生成。"""
    try:
        created = get_store().create(
            title=request.title,
            owner_id=user["id"],
            description=request.description,
            simulation_type=request.simulationType,
            is_private=request.isPrivate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ProjectResponse(**created)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, user: dict = OwnedUser):
    """
    取回自己的某个项目（无主项目也可读）。

    不属于当前用户且不是无主项目时返回 **404**（而不是 403）：403 等于确认
    "这个 id 存在，只是不是你的"，可以被用来探测别人有哪些项目。
    """
    project = get_store().get_project(project_id, user["id"], include_unowned=True)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return ProjectResponse(**project)


@router.post("/projects/{project_id}/claim", response_model=ProjectResponse)
async def claim_project(project_id: str, user: dict = OwnedUser):
    """
    认领一个**无主**项目（接上登录之前创建的数据）。

    刻意做成**显式操作**而不是"第一个注册的用户自动接管"：自动接管会静默改变
    数据归属，本项目已经因此把开发者手工建的项目划给了测试账号。
    认领是幂等的——重复点击返回同一条记录，不报错。
    """
    claimed = get_store().claim(project_id, user["id"])
    if claimed is None:
        # 不存在，或者已经属于别人（两者都 404，不泄露归属）
        raise HTTPException(
            status_code=404,
            detail=f"项目不存在或已属于其他用户：{project_id}",
        )
    return ProjectResponse(**claimed)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str, request: ProjectUpdate, user: dict = OwnedUser
):
    """
    局部更新项目（改名/改描述/改类型/改可见性）。

    Pydantic 已保证"未知字段 422"；这里再保证"一个字段都没给"是 400，
    而不是静默地只刷新一下 ``updatedAt``。
    """
    fields = request.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="请求里没有任何要更新的字段（可改：title / description / "
                   "simulationType / isPrivate）",
        )

    try:
        updated = get_store().update(project_id, user["id"], **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if updated is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return ProjectResponse(**updated)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, user: dict = OwnedUser):
    """删除自己的项目；不存在或不属于自己时返回 404。"""
    if not get_store().delete(project_id, user["id"]):
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return {"deleted": True, "id": project_id}


@router.get("/project-metadata")
async def project_metadata():
    """
    项目管理相关的元信息（供前端渲染下拉框，避免把可选值硬编码两份）。
    """
    return {
        "simulationTypes": list(SIMULATION_TYPES),
        "titleMaxLength": TITLE_MAX,
        "descriptionMaxLength": DESCRIPTION_MAX,
    }
