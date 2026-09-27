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

import json
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth import require_user
from config import SIMULATION_SETUP_MAX_BCS, SIMULATION_SETUP_MAX_BYTES, resolve_upload_path
from constraints import BoundaryCondition
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
    #: 是否保存过仿真配置（完整内容走 /setup 子资源，列表里不带，避免响应过大）
    hasSetup: bool = False
    #: ISO-8601 UTC 字符串；前端用 new Date(...) 解析
    createdAt: str
    updatedAt: str


# ------------------------------------------------------- 仿真配置（项目文档）

class SimulationSetup(BaseModel):
    """
    项目的仿真配置——几何、材料、边界条件、网格与求解设置的一份快照。

    校验策略是**分级**的，这一点是刻意的：

    - **后端自己要消费的字段从严**：`geometryFilename` 会用与上传同样的规则校验
      （纯文件名 + 扩展名白名单），因为前端之后会拿它去请求几何端点；
      `boundaryConditions` 直接用 `BoundaryCondition` 校验（`extra="forbid"`），
      它们会被原样送回求解器。
    - **纯前端 UI 设置从宽但有界**：`meshSettings` / `solverSettings` 只要求是
      JSON 对象，不逐字段校验。它们属于界面状态，形状会随界面迭代而变化，
      在这里钉死会把后端和前端 UI 耦合起来——**每次改界面都要同时改后端**。
      代价是这些字段存进去什么就是什么，因此用总量上限兜底。
    """

    #: 配置文档版本。将来形状变化时靠它做迁移，而不是猜。
    version: Literal[1] = 1
    #: 几何文件名（与上传目录里的名字一致）；没导入几何时为 None
    geometryFilename: Optional[str] = None
    #: 材料 id；材料库允许删除，所以这里不要求在库中存在
    materialId: Optional[str] = Field(default=None, max_length=64)
    #: 边界条件（会被原样送回求解器，因此严格校验）
    boundaryConditions: List[BoundaryCondition] = []
    #: 网格与求解设置（纯界面状态，从宽）
    meshSettings: Optional[Dict[str, Any]] = None
    solverSettings: Optional[Dict[str, Any]] = None

    @field_validator("geometryFilename")
    @classmethod
    def _validate_geometry(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        # 复用上传时的同一套规则：拒绝路径穿越/盘符/非法扩展名。
        # 这里**不检查文件是否存在**——几何可能已被清理，但配置仍应能保存下来
        # （用户的边界条件不该因为文件没了就丢掉）。
        try:
            resolve_upload_path(value)
        except ValueError as exc:
            raise ValueError(f"geometryFilename 不合法：{exc}")
        return value

    @field_validator("boundaryConditions")
    @classmethod
    def _limit_boundary_conditions(cls, value):
        if len(value) > SIMULATION_SETUP_MAX_BCS:
            raise ValueError(
                f"边界条件条数超过上限（{SIMULATION_SETUP_MAX_BCS}）"
            )
        return value


class SimulationSetupResponse(BaseModel):
    """``GET /setup`` 的返回。项目存在但没配过时 ``setup`` 为 ``null``（200，不是 404）。"""

    setup: Optional[SimulationSetup] = None
    #: 上次保存时间；从未保存过为 None
    savedAt: Optional[str] = None


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


# --------------------------------------------------------------- 仿真配置
#
# 为什么是独立子资源而不是塞进项目对象：
# 配置可能几十 KB（几何是边界条件 + 网格/求解设置的快照），而 `/api/projects`
# 是列表接口——把配置塞进去会让列表响应成倍变大。列表只需要一个 `hasSetup`
# 标记，完整内容按需取。


@router.get("/projects/{project_id}/setup", response_model=SimulationSetupResponse)
async def get_project_setup(project_id: str, user: dict = OwnedUser):
    """
    取项目的仿真配置。

    项目不存在/不属于自己 → **404**；
    项目存在但从未保存过配置 → **200 且 ``setup`` 为 ``null``**。
    两者必须分开：混起来前端会把"还没配过"当成"项目没了"。
    """
    stored = get_store().get_setup(project_id, user["id"])
    if stored is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return SimulationSetupResponse(**stored)


@router.put("/projects/{project_id}/setup", response_model=SimulationSetupResponse)
async def put_project_setup(
    project_id: str, request: SimulationSetup, user: dict = OwnedUser
):
    """
    覆盖保存仿真配置（整份替换，不做局部合并）。

    为什么整份覆盖：配置是一份**文档**，前端发来的本来就是完整状态。
    局部合并反而表达不了"删掉一个边界条件"。

    超过大小上限返回 **413**：配置是前端 UI 状态的快照，正常只有几 KB，
    超限说明发来的东西不对（或者有人想拿它当文件存储用）。
    """
    # exclude_none=True：不存 None 值。它们与"没有这个字段"等价，但会让文档膨胀
    # ——每个边界条件会多出十来个 null（Pydantic 会把所有未填的可选字段补成 None）
    # ——并且让"存进去什么、读回来就是什么"不再成立。
    # 实测：不用它时，前端发去 2 个边界条件，读回来每个都多了 10 个 null 字段。
    payload = request.model_dump(exclude_none=True)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > SIMULATION_SETUP_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"仿真配置过大（{size} 字节 > {SIMULATION_SETUP_MAX_BYTES} 字节）"
            ),
        )

    saved = get_store().set_setup(project_id, payload, user["id"])
    if saved is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return SimulationSetupResponse(**saved)


@router.delete("/projects/{project_id}/setup")
async def delete_project_setup(project_id: str, user: dict = OwnedUser):
    """
    清空仿真配置（保留项目本身）。

    用途：把项目重置为空工作台。**不做成"删项目"**——用户想重配一遍，
    不该连项目名和描述一起丢掉。
    """
    if not get_store().clear_setup(project_id, user["id"]):
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return {"cleared": True, "id": project_id}


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
