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
import functools
import threading
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


class ResultRetentionTest(unittest.TestCase):
    """
    结果按**体积**保留，而不只是按条数。

    为什么这值得一组测试：一条模态结果可以是几十 MB（8 阶振型 × 十几万节点 × 3
    分量），只按条数淘汰意味着"最近 12 条全留着"，最坏几百 MB 且**只增不减**。
    内存问题如果不可观测也不可断言，就只能等它把进程吃掉。
    """

    def setUp(self):
        # 这些常量是**运行期策略**，测试里改小以便用几 MB 的数据验证几十 MB 的行为
        self._saved = (
            jobs.MAX_RETAINED_RESULT_BYTES,
            jobs.MAX_SINGLE_RESULT_BYTES,
            jobs.MAX_JOBS,
        )
        self._saved_jobs = dict(jobs._JOBS)          # noqa: SLF001 - 测试要能清场
        self._saved_order = list(jobs._JOB_ORDER)    # noqa: SLF001
        # 每个测试都从**空表**开始：任务表是模块级全局状态，前面测试留下的记录
        # （以及它们被丢弃的结果）会让"数一数有几个被丢弃"这类断言失真——
        # 第一版就是这样数到了 6 个而不是 1 个。
        jobs._JOBS.clear()                           # noqa: SLF001
        jobs._JOB_ORDER.clear()                      # noqa: SLF001

    def tearDown(self):
        (
            jobs.MAX_RETAINED_RESULT_BYTES,
            jobs.MAX_SINGLE_RESULT_BYTES,
            jobs.MAX_JOBS,
        ) = self._saved
        jobs._JOBS.clear()                           # noqa: SLF001
        jobs._JOBS.update(self._saved_jobs)          # noqa: SLF001
        jobs._JOB_ORDER[:] = self._saved_order       # noqa: SLF001

    # ------------------------------------------------------- 估算器
    def test_estimator_grows_with_the_data(self):
        small = jobs.estimate_result_bytes([[1.0, 2.0, 3.0]] * 10)
        big = jobs.estimate_result_bytes([[1.0, 2.0, 3.0]] * 10000)
        self.assertGreater(big, small * 100, f"{small} -> {big}")

    def test_estimator_handles_the_modal_shape(self):
        """`mode_shapes` 的形状（阶数 × 节点 × 3 分量）要估到正确的量级。"""
        modes = [[[0.1, 0.2, 0.3] for _ in range(1000)] for _ in range(8)]
        estimated = jobs.estimate_result_bytes(modes)
        # 真正的浮点字节数是 8×1000×3×8 = 192 KB；估算允许偏大（抽样外推 + 结构开销），
        # 但不该差一个量级
        self.assertGreater(estimated, 192 * 1024)
        self.assertLess(estimated, 10 * 192 * 1024)

    def test_estimator_does_not_serialise(self):
        """
        估算必须是**便宜的**——它的全部意义就是避免为了"知道多大"而先序列化一遍
        （那会把内存翻倍，正好和目的相反）。这里用一个 500 万元素的嵌套数组计时。
        """
        big = [[0.0] * 5000 for _ in range(1000)]
        started = time.time()
        jobs.estimate_result_bytes(big)
        self.assertLess(time.time() - started, 0.5, "估算不该遍历整个数组")

    def test_estimator_handles_odd_values(self):
        self.assertEqual(jobs.estimate_result_bytes(None), 64)
        self.assertEqual(jobs.estimate_result_bytes("abc"), 3)
        self.assertGreater(jobs.estimate_result_bytes({"a": [1, 2]}), 0)

    # ------------------------------------------------------- 单条上限
    def test_oversized_single_result_is_not_kept(self):
        jobs.MAX_SINGLE_RESULT_BYTES = 64 * 1024        # 64 KB

        async def work():
            return {"blob": [0.0] * 100_000}            # 约 800 KB

        finished = _wait(submit("unit-test", work).id)
        # **状态仍是 succeeded**：事情算完了，只是结果没能留下
        self.assertEqual(finished.status, "succeeded")
        self.assertIsNone(finished.result)
        self.assertTrue(finished.result_dropped)
        self.assertGreater(finished.result_bytes, jobs.MAX_SINGLE_RESULT_BYTES)
        # 前端据此区分"没有结果"与"结果被回收了"
        payload = finished.to_dict()
        self.assertTrue(payload["resultDropped"])
        self.assertGreater(payload["resultBytes"], 0)

    def test_small_result_is_kept(self):
        jobs.MAX_SINGLE_RESULT_BYTES = 64 * 1024

        async def work():
            return {"answer": 42}

        finished = _wait(submit("unit-test", work).id)
        self.assertEqual(finished.result, {"answer": 42})
        self.assertFalse(finished.result_dropped)

    # ------------------------------------------------------- 总体预算
    def test_budget_releases_the_oldest_results(self):
        jobs.MAX_JOBS = 20
        jobs.MAX_SINGLE_RESULT_BYTES = 1024 * 1024      # 单条 1 MB（不会被单条上限丢）
        jobs.MAX_RETAINED_RESULT_BYTES = 2 * 1024 * 1024  # 总量 2 MB

        async def work(tag: str):
            return {"tag": tag, "blob": [0.0] * 30_000}   # 每条约 240 KB

        for index in range(12):
            _wait(submit("unit-test", functools.partial(work, str(index))).id)

        retained = sum(
            job.result_bytes for job in jobs._JOBS.values() if job.result is not None
        )                                                # noqa: SLF001
        self.assertLessEqual(
            retained, jobs.MAX_RETAINED_RESULT_BYTES,
            "回收之后总量必须落回预算内（这是它的全部意义）",
        )
        dropped = [job for job in jobs._JOBS.values() if job.result_dropped]  # noqa: SLF001
        self.assertTrue(dropped, "超出预算时应该有结果被回收")
        # 被回收的是**最旧的**，最新的几条必须还在（客户端刚提交就要取回结果）
        for job in jobs.list_jobs()[: jobs.MIN_RESULTS_KEPT]:
            self.assertIsNotNone(job.result, "最新的结果不能被回收掉")

    def test_budget_keeps_at_least_the_newest_results(self):
        """
        无论预算多小，`MIN_RESULTS_KEPT` 条最新结果都保留。

        轮询是"提交后几秒内取回结果"，如果刚算完就被别的大结果挤掉，
        那个客户端会拿到一个空结果——而这个下限让常见路径不受预算影响。
        """
        jobs.MAX_JOBS = 10
        jobs.MAX_SINGLE_RESULT_BYTES = 1024 * 1024
        jobs.MAX_RETAINED_RESULT_BYTES = 1              # 预算故意小到不可能满足

        async def work(tag: str):
            return {"tag": tag, "blob": [0.0] * 30_000}

        for index in range(5):
            _wait(submit("unit-test", functools.partial(work, str(index))).id)

        kept = [job for job in jobs.list_jobs() if job.result is not None]
        self.assertGreaterEqual(len(kept), jobs.MIN_RESULTS_KEPT)

    def test_single_cap_and_budget_are_consistent(self):
        """
        预算必须是**可证明**的硬界：`单条上限 × 至少保留条数 ≤ 总预算`。

        不满足这条的话，"总量不超过预算"就是假的——两条各自合法的结果就足以
        超过预算，而代码还认为自己守住了。
        """
        self.assertLessEqual(
            jobs.MAX_SINGLE_RESULT_BYTES * jobs.MIN_RESULTS_KEPT,
            jobs.MAX_RETAINED_RESULT_BYTES,
            "单条上限 × 至少保留条数 必须 ≤ 总预算，否则预算守不住",
        )

    # ------------------------------------------------------- 可观测
    def test_stats_report_retained_bytes(self):
        jobs.MAX_SINGLE_RESULT_BYTES = 64 * 1024

        async def small():
            return {"answer": 1}

        async def huge():
            return {"blob": [0.0] * 100_000}

        _wait(submit("unit-test", small).id)
        _wait(submit("unit-test", huge).id)
        stats = jobs.executor_stats()
        self.assertIn("retained_result_bytes", stats)
        self.assertIn("max_retained_result_bytes", stats)
        self.assertEqual(stats["results_dropped"], 1)
        self.assertGreater(stats["retained_result_bytes"], 0)

    def test_running_jobs_are_never_evicted(self):
        """
        回收只针对**已完成且有结果**的任务：排队/运行中的任务没有结果可回收，
        也绝不能被当成"空的旧任务"删掉——删了客户端就再也查不到自己的任务了。
        """
        jobs.MAX_SINGLE_RESULT_BYTES = 64 * 1024
        jobs.MAX_RETAINED_RESULT_BYTES = 1024

        started = threading.Event()
        release = threading.Event()

        async def slow():
            started.set()
            await asyncio.get_running_loop().run_in_executor(None, release.wait)
            return {"blob": [0.0] * 100_000}

        job = submit("unit-test", slow)
        self.assertTrue(started.wait(timeout=5))
        try:
            jobs.release_results_over_budget()
            self.assertIn(job.id, jobs._JOBS, "运行中的任务不能被回收")  # noqa: SLF001
        finally:
            release.set()
        _wait(job.id)



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
