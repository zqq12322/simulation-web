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
from auth_store import get_store as get_auth_store
from config import SIMULATION_SETUP_MAX_BCS, SIMULATION_SETUP_MAX_BYTES, resolve_upload_path
from constraints import BoundaryCondition
from logging_config import get_logger
from project_store import ROLE_OWNER, SHARE_ROLES, SIMULATION_TYPES, get_store
from runs import get_run_store

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
    #: 当前请求者对这个项目的角色：owner / editor / viewer / unowned。
    #: 界面据此决定"能不能改、能不能删、要不要显示只读提示"。
    role: Optional[Literal["owner", "editor", "viewer", "unowned"]] = None
    #: ISO-8601 UTC 字符串；前端用 new Date(...) 解析
    createdAt: str
    updatedAt: str


# --------------------------------------------------------------- 共享

class ShareInvite(BaseModel):
    """按**用户名**共享（用户知道的是用户名，不是那串随机 id）。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., max_length=64)
    role: Literal["viewer", "editor"] = "viewer"


class ShareResponse(BaseModel):
    """一条共享记录。**不含任何口令相关字段**。"""

    userId: str
    username: str
    displayName: str
    role: Literal["viewer", "editor"]
    createdAt: str


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
    列出**当前用户能看到**的项目，最新建的在前。

    包含三类，每条都带 ``role``：

    - 自己的（``owner``）
    - 共享给自己的（``editor`` / ``viewer``）——"Shared with me"
    - **无主的**遗留项目（``unowned``）：接上登录之前创建的数据，对已登录用户
      可见但不可改，需要显式调用 ``POST /api/projects/{id}/claim`` 认领
    """
    return [
        ProjectResponse(**item) for item in get_store().list_visible(user["id"])
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
    取回一个项目——**自己有权限看到的都行**：自己的、共享给自己的、无主的。

    无权访问时返回 **404**（而不是 403）：403 等于确认"这个 id 存在，
    只是不是你的"，可以被用来探测别人有哪些项目。
    """
    project = get_store().get_visible(project_id, user["id"])
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

    **只有属主**能改这些元信息：改名属于"管理项目"，与"改仿真配置"是两回事
    （后者 editor 也能做）。存储层的 SQL 就是按属主限定的，所以非属主会拿到 404。

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
    # 求解记录跟着一起删：留着就是孤儿行，会一直占空间且再也无人能访问
    # （运行记录端点第一步就要查项目权限，项目没了就永远是 404）。
    removed = get_run_store().delete_for_project(project_id)
    if removed:
        logger.info("删除项目 %s 时一并清掉 %d 条运行记录", project_id, removed)
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

    **有读权限就能取**：自己的、共享给自己的（viewer / editor）、无主的。
    无权访问 → **404**；项目存在但从未保存过配置 → **200 且 ``setup`` 为 null**。
    两者必须分开：混起来前端会把"还没配过"当成"项目没了"。
    """
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if not store.can_read(role):
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

    stored = store.get_setup(project_id, store_owner_id(project_id))
    if stored is None:  # pragma: no cover - 上面已确认项目存在
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return SimulationSetupResponse(**stored)


def store_owner_id(project_id: str) -> Optional[str]:
    """
    取项目的属主 id（用于复用按属主限定的存储方法）。

    读/写配置本来就只要求"有相应权限"，而存储层的 `get_setup`/`set_setup` 是按
    属主限定的，因此权限通过后用属主身份调它们即可——这样"配置属于项目、
    项目属于属主"这条关系在存储层保持一致，不必再造一套"按角色访问"的查询。
    """
    with get_store()._cursor() as connection:      # noqa: SLF001 - 同模块内的受控用法
        row = connection.execute(
            "SELECT owner_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return row["owner_id"] if row else None


@router.put("/projects/{project_id}/setup", response_model=SimulationSetupResponse)
async def put_project_setup(
    project_id: str, request: SimulationSetup, user: dict = OwnedUser
):
    """
    覆盖保存仿真配置（整份替换，不做局部合并）。

    **属主与 editor 都能保存**——这正是"共享来一起做"的含义。viewer 与无主项目
    的访问者会被拒（403）。

    为什么这里用 403 而不是 404：能走到这一步的人**已经知道项目存在**
    （他在列表里看得见、也读得到配置），此时再返回 404 只会让人困惑。
    404 是用来对"不知道存不存在"的人隐藏信息的。
    """
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    if not store.can_edit(role):
        raise HTTPException(
            status_code=403,
            detail=f"你对这个项目只有{'只读' if role == 'viewer' else '认领前'}权限，"
                   "无法修改配置",
        )

    # exclude_none=True：不存 None 值。它们与"没有这个字段"等价，但会让文档膨胀
    # ——每个边界条件会多出十来个 null（Pydantic 会把所有未填的可选字段补成 None）
    # ——并且让"存进去什么、读回来就是什么"不再成立。
    payload = request.model_dump(exclude_none=True)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > SIMULATION_SETUP_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"仿真配置过大（{size} 字节 > {SIMULATION_SETUP_MAX_BYTES} 字节）"
            ),
        )

    saved = store.set_setup(project_id, payload, store_owner_id(project_id))
    if saved is None:  # pragma: no cover - 上面已确认项目存在
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return SimulationSetupResponse(**saved)


@router.delete("/projects/{project_id}/setup")
async def delete_project_setup(project_id: str, user: dict = OwnedUser):
    """
    清空仿真配置（保留项目本身）。

    用途：把项目重置为空工作台。**不做成"删项目"**——用户想重配一遍，
    不该连项目名和描述一起丢掉。

    **只有属主**能清空：清空是破坏性的（会丢掉别人配好的东西），
    因此不因为"是 editor"就放行。
    """
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    if not store.can_manage(role):
        raise HTTPException(status_code=403, detail="只有项目属主可以清空配置")

    if not store.clear_setup(project_id, store_owner_id(project_id)):
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return {"cleared": True, "id": project_id}


# --------------------------------------------------------------- 共享端点
#
# 权限规则：
# - 管理共享（加人、改角色、踢人）**只属于属主**；
# - 被共享者可以把自己**移除**（leave），否则他没法退出一个共享；
# - 共享用**用户名**而不是用户 id：人知道的是用户名。


def _resolve_share_target(username: str) -> dict:
    """按用户名找用户；找不到返回 404（而不是 400——这是"没有人"而不是"参数错"）。"""
    user = get_auth_store().get_user_by_username(username.strip().lower())
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户不存在：{username}")
    return user


def _share_response(record: dict) -> ShareResponse:
    """把存储记录补上用户名/显示名。**绝不带口令字段**。"""
    target = get_auth_store().get_user(record["userId"])
    return ShareResponse(
        userId=record["userId"],
        username=target["username"] if target else "（已注销）",
        displayName=target["displayName"] if target else "（已注销）",
        role=record["role"],
        createdAt=record["createdAt"],
    )


@router.get("/projects/{project_id}/shares", response_model=List[ShareResponse])
async def list_project_shares(project_id: str, user: dict = OwnedUser):
    """列出共享名单。**只有属主**看得到（被共享者不需要知道还有谁）。"""
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    if not store.can_manage(role):
        raise HTTPException(status_code=403, detail="只有项目属主可以查看共享名单")
    return [_share_response(item) for item in store.list_shares(project_id)]


@router.post("/projects/{project_id}/shares", response_model=ShareResponse,
             status_code=201)
async def create_project_share(
    project_id: str, request: ShareInvite, user: dict = OwnedUser
):
    """把项目共享给某个用户（按用户名）。重复共享会更新角色，不会堆出重复条目。"""
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    if not store.can_manage(role):
        raise HTTPException(status_code=403, detail="只有项目属主可以共享项目")

    target = _resolve_share_target(request.username)
    if target["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="不能共享给自己")

    record = store.share(project_id, target["id"], request.role, invited_by=user["id"])
    return _share_response(record)


@router.delete("/projects/{project_id}/shares/{target_user_id}")
async def delete_project_share(
    project_id: str, target_user_id: str, user: dict = OwnedUser
):
    """
    取消共享。

    - 属主可以移除任何人；
    - 被共享者可以**移除自己**（退出共享）——否则他没法退出一个共享。

    既不是属主、又不是在移除自己 → 403。
    """
    store = get_store()
    role = store.access_role(project_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")

    removing_self = target_user_id == user["id"]
    if not (store.can_manage(role) or removing_self):
        raise HTTPException(status_code=403, detail="只有项目属主可以移除其他协作者")

    if not store.unshare(project_id, target_user_id):
        raise HTTPException(status_code=404, detail="该用户没有被共享过这个项目")
    return {"removed": True, "projectId": project_id, "userId": target_user_id}


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
