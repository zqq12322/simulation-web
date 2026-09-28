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

#: 保留结果的**体积**上限（不只是条数）。
#:
#: 为什么必须有这一条：一条模态结果可以是几十 MB（8 阶振型 × 十几万节点 × 3 分量）。
#: 只按条数淘汰意味着"最近 12 条结果全留着"——最坏情况是几百 MB，而且**只增不减**，
#: 长时间运行的后端迟早在这上面出事。（实测确实见过一次：跑了很久的 dev 后端在
#: 处理 16 万单元网格时静默死亡，日志无 traceback。**没有复现，所以不能说已定位**，
#: 但"结果无界保留"本身是真的。）
MAX_RETAINED_RESULT_BYTES = 64 * 1024 * 1024

#: 单条结果超过它就**不保留内容**（只留状态、大小与 `resultDropped` 标记）。
#: 这种量级的结果传给浏览器本来也不可用，而留在进程里足以把内存吃掉。
MAX_SINGLE_RESULT_BYTES = 32 * 1024 * 1024

#: 无论体积预算如何，**至少**保留最近这么多条结果。
#: 轮询是"提交后几秒内取回结果"，如果刚算完就被别的大结果挤掉，那个客户端会拿到
#: 一个空结果——而这个下限让常见路径不受预算影响。
MIN_RESULTS_KEPT = 2

_JOB_LOCK = threading.Lock()
_JOBS: Dict[str, "Job"] = {}
_JOB_ORDER: List[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_result_bytes(value: Any, depth: int = 0) -> int:
    """
    粗估一个结果占多少字节。

    **故意不精确**：精确测量得先 `json.dumps` 一遍，而那会真的再分配几十 MB——
    为了知道"结果有多大"而先把内存翻倍，正好和这里的目的相反。

    做法：
    - 字典/列表递归，字符串按 UTF-8 长度，数值按 8 字节；
    - **大列表只抽样第一个元素**外推其余。遍历一个 500 万元素的嵌套数组在 Python
      里要好几秒，而这里只需要一个"量级正确"的估计；
    - 深度超过 8 层不再往下（深到那个程度的数值结构没有别的情况）。
    """
    if depth > 8:
        return 64
    if isinstance(value, dict):
        return 64 + sum(
            len(str(key)) + estimate_result_bytes(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        size = 8 * len(value)
        if value:
            size += len(value) * estimate_result_bytes(value[0], depth + 1)
        return size
    if isinstance(value, str):
        return len(value.encode("utf-8", "replace"))
    if isinstance(value, bool):
        return 4
    if isinstance(value, (int, float)):
        return 8
    return 64


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
    #: 结果体积的**估计值**（见 `estimate_result_bytes`）
    result_bytes: int = 0
    #: 结果内容是否已被丢弃（超过单条上限，或为腾出体积预算被淘汰）。
    #: 有它前端才能区分"这次没有结果"和"结果被回收了"。
    result_dropped: bool = False

    def to_dict(self, include_result: bool = True) -> dict:
        payload = {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "resultBytes": self.result_bytes,
            "resultDropped": self.result_dropped,
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


def release_results_over_budget() -> int:
    """
    按体积预算回收**已完成任务**的结果内容（从最旧的开始），返回回收条数。

    只在体积超预算时动手；`MIN_RESULTS_KEPT` 条最新的结果永远保留——轮询是
    "提交后几秒内取回结果"，不能让刚算完的那条被别的大结果挤掉。
    运行中/排队中的任务没有结果，自然不参与。
    """
    released = 0
    with _JOB_LOCK:
        finished = [
            _JOBS[job_id]
            for job_id in reversed(_JOB_ORDER)          # 最新在前
            if job_id in _JOBS and _JOBS[job_id].result is not None
        ]
        retained = sum(job.result_bytes for job in finished)
        if retained <= MAX_RETAINED_RESULT_BYTES:
            return 0
        for job in finished[MIN_RESULTS_KEPT:]:
            if retained <= MAX_RETAINED_RESULT_BYTES:
                break
            retained -= job.result_bytes
            job.result = None
            job.result_dropped = True
            released += 1
    if released:
        logger.info(
            "任务结果超出体积预算（%d MB），回收了 %d 条最旧的结果",
            MAX_RETAINED_RESULT_BYTES // (1024 * 1024), released,
        )
    return released


def _execute(job: Job, coro_factory: Callable[[], Any]) -> None:
    job.status = "running"
    job.started_at = _now()
    logger.info("任务 %s（%s）开始", job.id, job.kind)
    try:
        value = _run_sync(coro_factory)
        # pydantic 模型转成可 JSON 序列化的结构
        job.result = value.model_dump() if hasattr(value, "model_dump") else value
        job.result_bytes = estimate_result_bytes(job.result)
        # 单条就超上限：不保留内容。这种量级传给浏览器本来也不可用，
        # 而留在进程里足以把内存吃掉。**状态仍然是 succeeded**——事情算完了，
        # 只是结果没能留下；`resultDropped` 让前端能如实说明这一点。
        if job.result_bytes > MAX_SINGLE_RESULT_BYTES:
            logger.warning(
                "任务 %s（%s）的结果约 %d MB，超过单条上限 %d MB，不保留内容",
                job.id, job.kind,
                job.result_bytes // (1024 * 1024),
                MAX_SINGLE_RESULT_BYTES // (1024 * 1024),
            )
            job.result = None
            job.result_dropped = True
        job.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 - 任务边界，任何异常都要落到任务状态里
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        logger.exception("任务 %s（%s）失败", job.id, job.kind)
    finally:
        job.finished_at = _now()
        logger.info("任务 %s（%s）结束：%s", job.id, job.kind, job.status)
        release_results_over_budget()


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
    """
    给 `/api/jobs` 用的一点自省信息。

    **把"保留了多少结果"报出来**：内存问题如果不可观测，就只能等它把进程吃掉
    才发现。这两个数字是运维时唯一能提前看出问题的地方。
    """
    with _JOB_LOCK:
        counts: Dict[str, int] = {}
        retained_bytes = 0
        dropped = 0
        for job in _JOBS.values():
            counts[job.status] = counts.get(job.status, 0) + 1
            if job.result is not None:
                retained_bytes += job.result_bytes
            if job.result_dropped:
                dropped += 1
    return {
        "queued_or_running": counts.get("queued", 0) + counts.get("running", 0),
        "by_status": counts,
        "max_jobs_retained": MAX_JOBS,
        "retained_result_bytes": retained_bytes,
        "max_retained_result_bytes": MAX_RETAINED_RESULT_BYTES,
        "results_dropped": dropped,
    }


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
