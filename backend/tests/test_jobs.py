"""
后台任务（jobs.py）测试。

覆盖三件事：
1. 任务生命周期：queued → running → succeeded / failed，失败时错误要落到任务上；
2. 找不到的任务返回 404；
3. **单线程串行**：这是 gmsh 安全性的前提（gmsh 非线程安全），
   用并发计数器证明两个任务永不重叠；
4. 真实任务（网格 / 求解）的结果与同步接口一致。
"""

import asyncio
import time
import unittest

from fastapi import HTTPException

import jobs
from jobs import MeshJobRequest, get_job, get_job_status, submit, submit_mesh_job, submit_solve_job


def _wait(job_id, timeout=60.0):
    """轮询直到任务结束（任务被淘汰出任务表会明确报错，而不是干等到超时）。"""
    deadline = time.time() + timeout
    seen = False
    while time.time() < deadline:
        job = get_job(job_id)
        if job is not None:
            seen = True
            if job.status in ("succeeded", "failed"):
                return job
        elif seen:
            raise AssertionError(f"任务 {job_id} 已被淘汰出任务表（超过保留上限）")
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未结束")


class JobLifecycleTest(unittest.TestCase):
    def test_successful_job_records_result(self):
        async def work():
            await asyncio.sleep(0.01)
            return {"answer": 42}

        job = submit("unit-test", work)
        self.assertIn(job.status, ("queued", "running"))

        finished = _wait(job.id)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result, {"answer": 42})
        self.assertIsNotNone(finished.started_at)
        self.assertIsNotNone(finished.finished_at)
        self.assertIsNone(finished.error)

    def test_failed_job_records_error(self):
        async def boom():
            raise RuntimeError("故意失败")

        finished = _wait(submit("unit-test", boom).id)
        self.assertEqual(finished.status, "failed")
        self.assertIn("故意失败", finished.error)
        self.assertIsNone(finished.result)

    def test_pydantic_result_is_serialised(self):
        from geometry import GeometryMetadata

        async def work():
            return GeometryMetadata(faces=[], message="ok")

        finished = _wait(submit("unit-test", work).id)
        self.assertEqual(finished.status, "succeeded")
        self.assertIsInstance(finished.result, dict)
        self.assertEqual(finished.result["message"], "ok")

    def test_unknown_job_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_job_status("no-such-job"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_status_endpoint_shape(self):
        async def work():
            return {"ok": True}

        job_id = submit("unit-test", work).id
        payload = asyncio.run(get_job_status(job_id))
        for key in ("job_id", "kind", "status", "created_at", "result", "error"):
            self.assertIn(key, payload)
        _wait(job_id)

    def test_old_jobs_are_evicted(self):
        """任务表有上限：最旧的会被淘汰，避免结果（可能很大）把内存吃光。"""

        async def work():
            return None

        submitted = []
        for _ in range(jobs.MAX_JOBS + 3):
            job_id = submit("unit-test", work).id
            submitted.append(job_id)
            # 立刻等待：新提交的一定还在表里；否则等一个可能已被淘汰的任务会白等
            _wait(job_id, timeout=30)

        with jobs._JOB_LOCK:  # noqa: SLF001 - 测试内部状态
            retained = len(jobs._JOBS)
        self.assertLessEqual(retained, jobs.MAX_JOBS)
        self.assertIsNone(get_job(submitted[0]), "最旧的任务应已被淘汰")
        self.assertIsNotNone(get_job(submitted[-1]), "最新任务应当仍在表中")


class ExecutorSerialisationTest(unittest.TestCase):
    """
    单线程工作器必须让任务**严格串行**——这是 gmsh 安全的前提。
    """

    def test_jobs_never_overlap(self):
        active = 0
        peak = 0

        async def work():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)   # 若并发，这里就会重叠
            active -= 1
            return None

        submitted = [submit("unit-test", work).id for _ in range(4)]
        for job_id in submitted:
            _wait(job_id, timeout=30)

        self.assertEqual(peak, 1, f"任务出现了重叠执行（峰值并发 {peak}）")


class RealJobTest(unittest.TestCase):
    """真实任务：异步接口的结果必须与同步接口一致。"""

    def test_mesh_job_matches_sync_endpoint(self):
        from geometry import generate_mesh

        payload = asyncio.run(submit_mesh_job(MeshJobRequest(filename="default_cube.step", mesh_size=1.5)))
        job = _wait(payload["job_id"])
        self.assertEqual(job.status, "succeeded", job.error)

        sync_result = asyncio.run(generate_mesh("default_cube.step", 1.5))
        self.assertEqual(len(job.result["nodes"]), len(sync_result.nodes))
        self.assertEqual(len(job.result["elements"]), len(sync_result.elements))
        self.assertEqual(len(job.result["faces"]), len(sync_result.faces))

    def test_solve_job_matches_sync_endpoint(self):
        from constraints import BoundaryCondition
        from geometry import generate_mesh
        from solver import SolverRequest, solve_simulation

        mesh = asyncio.run(generate_mesh("default_cube.step", 1.5))

        def face(axis, sign):
            return next(f for f in mesh.faces if f.normal and f.normal[axis] * sign > 0.9)

        payload = {
            "geometry_filename": "default_cube.step",
            "material_id": "structural_steel",
            "length_unit": "m",
            "boundary_conditions": [
                {"id": "fix", "name": "固定端", "type": "fixed",
                 "applicationType": "face", "entityIndex": face(0, -1).id},
                {"id": "pull", "name": "拉力", "type": "force",
                 "applicationType": "face", "entityIndex": face(0, 1).id,
                 "force": {"x": 1000.0, "y": 0.0, "z": 0.0}},
            ],
            "faces": [f.model_dump() for f in mesh.faces],
        }

        submitted = asyncio.run(submit_solve_job(payload))
        job = _wait(submitted["job_id"], timeout=120)
        self.assertEqual(job.status, "succeeded", job.error)

        sync_result = asyncio.run(solve_simulation(SolverRequest(**payload)))
        self.assertAlmostEqual(
            job.result["max_stress"], sync_result.max_stress,
            delta=abs(sync_result.max_stress) * 1e-9 + 1e-9,
        )
        self.assertEqual(len(job.result["stresses"]), len(sync_result.stresses))

    def test_invalid_solve_payload_returns_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_solve_job({"geometry_filename": "x.step"}))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
