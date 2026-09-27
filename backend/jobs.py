"""
轻量级后台任务（不引入 Redis / Celery）。

为什么需要
----------
网格划分与求解是 CPU 密集且耗时不确定的操作，此前直接在 HTTP 请求里同步执行：
大模型会让请求超时，浏览器或网关先断开，用户拿到 502——而任务其实还在算。
现在可以「提交任务 → 拿 job id → 轮询进度」。

设计取舍
--------
1. **单线程执行器**：gmsh 的 Python API 背后是进程级全局状态（当前模型、选项、
   网格），**不是线程安全的**。让所有 gmsh 操作都在同一个工作线程里排队执行，
   既天然串行（不会有并发破坏），又不需要在业务代码里到处加锁。
   同步端点也走这条队列（`run_in_worker`），所以"同步请求 + 后台任务"不会并发。
2. **进程内状态**：任务表存在内存里，服务重启即丢。这对当前阶段够用；
   真正的生产方案要外部队列（Redis/RQ），但那样会显著提高部署门槛，
   现阶段不值得——已把这条限制写进文档。
3. **结果保留上限**：网格/求解结果可能很大（几十万节点的数组），
   因此任务表只保留最近若干个，避免内存无上限增长。
"""

from __future__ import annotations

import asyncio
import functools
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from logging_config import get_logger

logger = get_logger(__name__)

#: 单线程：见模块文档第 1 条（gmsh 非线程安全）
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simcloud-worker")

#: 任务表上限（结果可能很大，只保留最近的若干个）
MAX_JOBS = 12

_JOB_LOCK = threading.Lock()
_JOBS: Dict[str, "Job"] = {}
_JOB_ORDER: List[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """一个后台任务的记录。"""

    id: str
    kind: str
    status: str = "queued"          # queued | running | succeeded | failed
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Any = None
    error: Optional[str] = None

    def to_dict(self, include_result: bool = True) -> dict:
        payload = {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }
        if include_result:
            payload["result"] = self.result
        return payload


def _register(job: Job) -> None:
    with _JOB_LOCK:
        _JOBS[job.id] = job
        _JOB_ORDER.append(job.id)
        # 超出上限就丢弃最旧的（连同它的大结果一起释放）
        while len(_JOB_ORDER) > MAX_JOBS:
            oldest = _JOB_ORDER.pop(0)
            _JOBS.pop(oldest, None)


def get_job(job_id: str) -> Optional[Job]:
    with _JOB_LOCK:
        return _JOBS.get(job_id)


def list_jobs() -> List[Job]:
    with _JOB_LOCK:
        return [_JOBS[job_id] for job_id in reversed(_JOB_ORDER) if job_id in _JOBS]


def _run_sync(coro_factory: Callable[[], Any]) -> Any:
    """在工作线程里执行一个协程函数（线程内自建事件循环）。"""
    return asyncio.run(coro_factory())


def _execute(job: Job, coro_factory: Callable[[], Any]) -> None:
    job.status = "running"
    job.started_at = _now()
    logger.info("任务 %s（%s）开始", job.id, job.kind)
    try:
        value = _run_sync(coro_factory)
        # pydantic 模型转成可 JSON 序列化的结构
        job.result = value.model_dump() if hasattr(value, "model_dump") else value
        job.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 - 任务边界，任何异常都要落到任务状态里
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        logger.exception("任务 %s（%s）失败", job.id, job.kind)
    finally:
        job.finished_at = _now()
        logger.info("任务 %s（%s）结束：%s", job.id, job.kind, job.status)


def submit(kind: str, coro_factory: Callable[[], Any]) -> Job:
    """
    把任务排进单线程队列并立即返回。

    ``coro_factory`` 是一个**无参函数**，调用后返回协程（用 functools.partial 包装即可）。
    """
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    _register(job)
    _EXECUTOR.submit(_execute, job, coro_factory)
    logger.info("已提交任务 %s（%s）", job.id, kind)
    return job


async def run_in_worker(async_fn: Callable, *args, **kwargs) -> Any:
    """
    把同步端点也送进同一条队列并**等待**结果（`await` 不阻塞事件循环）。

    这样"同步请求"和"后台任务"永远走同一个工作线程，不会并发碰 gmsh；
    而事件循环仍然可以继续服务其它请求（比如轮询任务状态）。
    """
    loop = asyncio.get_running_loop()
    factory = functools.partial(async_fn, *args, **kwargs)
    return await loop.run_in_executor(_EXECUTOR, _run_sync, factory)


def executor_stats() -> dict:
    """给 /api/jobs 用的一点自省信息。"""
    with _JOB_LOCK:
        counts: Dict[str, int] = {}
        for job in _JOBS.values():
            counts[job.status] = counts.get(job.status, 0) + 1
    return {"queued_or_running": counts.get("queued", 0) + counts.get("running", 0),
            "by_status": counts, "max_jobs_retained": MAX_JOBS}


# --------------------------------------------------------------------- 接口
from auth import require_user

#: 整个 router 都要求登录。用**路由级依赖**而不是给每个端点加参数：
#: 端点本身并不需要知道「你是谁」，而且 40 多个既有测试是**直接调用端点函数**的
#: （不经 HTTP），逐个加参数会让它们全部失效。
router = APIRouter(dependencies=[Depends(require_user)])



class MeshJobRequest(BaseModel):
    filename: str
    mesh_size: float = 0.5


@router.get("/jobs")
async def list_all_jobs():
    """列出最近的任务（新的在前）。便于排查"任务卡住了吗"。"""
    return {
        "jobs": [job.to_dict(include_result=False) for job in list_jobs()],
        "stats": executor_stats(),
    }


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    查询任务状态；成功时结果放在 ``result`` 字段里（结构与对应的同步接口一致）。
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    return job.to_dict()


@router.post("/jobs/generate-mesh", status_code=202)
async def submit_mesh_job(request: MeshJobRequest):
    """异步划分网格：立即返回 job_id，用 GET /api/jobs/{job_id} 轮询。"""
    from geometry import generate_mesh_impl

    job = submit(
        "generate-mesh",
        functools.partial(generate_mesh_impl, filename=request.filename, mesh_size=request.mesh_size),
    )
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/jobs/solve", status_code=202)
async def submit_solve_job(request: dict):
    """异步求解：立即返回 job_id，用 GET /api/jobs/{job_id} 轮询。"""
    from solver import SolverRequest, solve_impl

    try:
        solver_request = SolverRequest(**request)
    except Exception as exc:  # pydantic 校验失败 -> 400，而不是静默入队
        raise HTTPException(status_code=400, detail=f"请求体不合法：{exc}")

    job = submit("solve", functools.partial(solve_impl, request=solver_request))
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/jobs/thermal", status_code=202)
async def submit_thermal_job(request: dict):
    """异步稳态热传导求解：立即返回 job_id，用 GET /api/jobs/{job_id} 轮询。"""
    from thermal import ThermalRequest, solve_thermal_impl

    try:
        thermal_request = ThermalRequest(**request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"请求体不合法：{exc}")

    job = submit("thermal", functools.partial(solve_thermal_impl, request=thermal_request))
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/jobs/modal", status_code=202)
async def submit_modal_job(request: dict):
    """异步模态分析：立即返回 job_id，用 GET /api/jobs/{job_id} 轮询。"""
    from modal import ModalRequest, solve_modal_impl

    try:
        modal_request = ModalRequest(**request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"请求体不合法：{exc}")

    job = submit("modal", functools.partial(solve_modal_impl, request=modal_request))
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}


@router.post("/jobs/convergence", status_code=202)
async def submit_convergence_job(request: dict):
    """
    异步 h-收敛检查：立即返回 job_id，用 GET /api/jobs/{job_id} 轮询。

    这个接口**必须**是异步的：它要跑 3~4 次"划网格 + 求解"，而单元数按 h³
    增长——同步接口在大模型上必然超时（用户拿到 502，而任务其实还在算）。
    """
    from convergence_study import StudyRequest, study_impl

    try:
        study_request = StudyRequest(**request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"请求体不合法：{exc}")

    job = submit("convergence", functools.partial(study_impl, request=study_request))
    return {"job_id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"}
