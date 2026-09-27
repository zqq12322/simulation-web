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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    #: ISO-8601 UTC 字符串；前端用 new Date(...) 解析
    createdAt: str
    updatedAt: str


@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects():
    """列出全部项目，最新建的在前。"""
    return [ProjectResponse(**item) for item in get_store().list_projects()]


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectCreate):
    """新建项目。ID 与时间戳由服务端生成。"""
    try:
        created = get_store().create(
            title=request.title,
            description=request.description,
            simulation_type=request.simulationType,
            is_private=request.isPrivate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ProjectResponse(**created)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    project = get_store().get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return ProjectResponse(**project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdate):
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
        updated = get_store().update(project_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if updated is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return ProjectResponse(**updated)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """删除项目；不存在时返回 404（而不是假装删掉了）。"""
    if not get_store().delete(project_id):
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
