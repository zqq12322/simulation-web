"""
项目求解记录 API：`/api/projects/{id}/runs`。

权限沿用项目那一套（`ProjectStore.access_role` 是唯一入口）：

| 操作 | 要求 | 无权时 | 只读时 |
|---|---|---|---|
| 看记录 | `can_read`（属主 / editor / viewer / 无主） | 404 | — |
| 记一条 | `can_edit`（属主 / editor） | 404 | **403** |
| 删一条 | `can_edit` | 404 | **403** |

三处状态码刻意不同，与项目共享那一轮的理由一致：**无权访问的人不该知道项目
存在（404）；已经能看见项目、只是不能写的人，返回 404 只会让他困惑（403）**。

为什么"记一条"算编辑而不是"任何人可写"：记录会出现在这个项目的共享视图里，
它属于项目内容。viewer 只能看。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from auth import require_user
from logging_config import get_logger
from project_store import get_store
from run_store import (
    MAX_RUNS_PER_PROJECT,
    RUN_ANALYSIS_TYPES,
    RunStore,
    validate_run_summary,
)

logger = get_logger(__name__)
router = APIRouter()

#: 与项目端点一致：`user: dict = OwnedUser`
OwnedUser = Depends(require_user)

_run_store: Optional[RunStore] = None


def get_run_store() -> RunStore:
    """进程内单例（与 `project_store.get_store` 同一模式）。"""
    global _run_store
    if _run_store is None:
        _run_store = RunStore()
    return _run_store


class RunCreate(BaseModel):
    """记一条运行。**只提交数值**，不提交位移/应力数组（见 run_store 的说明）。"""

    model_config = ConfigDict(extra="forbid")

    analysisType: str
    #: 形如 {"max_stress": 6.5e7, "max_displacement": 2.2e-6}
    quantities: Dict[str, float]
    meshSize: Optional[float] = None
    elements: Optional[int] = None
    nodes: Optional[int] = None
    warnings: List[str] = []


class RunResponse(BaseModel):
    id: str
    projectId: str
    analysisType: str
    createdBy: Optional[str] = None
    createdAt: str
    summary: Dict[str, Any]
    #: 库里那条摘要的 JSON 坏了（例如手工改过库）时为 true，界面据此提示，
    #: 而不是把"空摘要"当成"这次什么都没算出来"
    summaryParseError: bool = False


class RunListResponse(BaseModel):
    runs: List[RunResponse]
    total: int
    limit: int


def _to_response(record: dict) -> RunResponse:
    return RunResponse(
        id=record["id"],
        projectId=record["project_id"],
        analysisType=record["analysis_type"],
        createdBy=record.get("created_by"),
        createdAt=record["created_at"],
        summary=record.get("summary") or {},
        summaryParseError=bool(record.get("summary_parse_error")),
    )


def _require_access(project_id: str, user_id: str, *, write: bool) -> str:
    """
    权限检查，返回角色。

    `write=True` 时要求编辑权：无权 → 404，只读 → 403。
    """
    store = get_store()
    role = store.access_role(project_id, user_id)
    if write:
        if role is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
        if not store.can_edit(role):
            raise HTTPException(
                status_code=403,
                detail=f"你对这个项目只有{'只读' if role == 'viewer' else '认领前'}权限，"
                       "无法记录或删除运行记录",
            )
        return role
    if not store.can_read(role):
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return role


@router.get("/projects/{project_id}/runs", response_model=RunListResponse)
async def list_project_runs(project_id: str, user: dict = OwnedUser):
    """
    列出项目的求解记录（**最新的在前**）。

    空列表是正常结果（"这个项目还没跑过"），不是 404——项目本身存在。
    """
    _require_access(project_id, user["id"], write=False)
    store = get_run_store()
    records = store.list_for_project(project_id)
    return RunListResponse(
        runs=[_to_response(record) for record in records],
        total=store.count_for_project(project_id),
        limit=MAX_RUNS_PER_PROJECT,
    )


@router.post("/projects/{project_id}/runs", response_model=RunResponse, status_code=201)
async def create_project_run(
    project_id: str, request: RunCreate, user: dict = OwnedUser
):
    """
    记一条运行。数值必须**有限**，且考察量集合必须与该分析类型声明的一致。

    拼错的字段会被拒绝而不是静默存下来——否则记录里那一项会一直是空的，
    而没人知道为什么。
    """
    _require_access(project_id, user["id"], write=True)

    if request.analysisType not in RUN_ANALYSIS_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"analysisType 只能是 {'/'.join(RUN_ANALYSIS_TYPES)}",
        )

    try:
        summary = validate_run_summary(
            request.analysisType,
            request.quantities,
            mesh_size=request.meshSize,
            elements=request.elements,
            nodes=request.nodes,
            warnings=request.warnings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    record = get_run_store().record(
        project_id=project_id,
        analysis_type=request.analysisType,
        summary=summary,
        created_by=user["id"],
    )
    return _to_response(record)


@router.delete("/projects/{project_id}/runs/{run_id}", status_code=204)
async def delete_project_run(
    project_id: str, run_id: str, user: dict = OwnedUser
):
    """删除一条记录。记录不属于该项目时返回 404（不做跨项目的删除）。"""
    _require_access(project_id, user["id"], write=True)
    if not get_run_store().delete(run_id, project_id=project_id):
        raise HTTPException(status_code=404, detail=f"运行记录不存在：{run_id}")
    return None
