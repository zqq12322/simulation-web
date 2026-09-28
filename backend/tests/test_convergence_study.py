"""
用户模型 h-收敛检查的测试。

测试的取舍和 `test_convergence.py` 不同：那里有精确解，可以断言**数值**；
这里没有精确解，所以断言的是**行为与诚实性**：

1. **网格尺寸的推导与校验**：默认逐级减半、显式序列必须严格递减、
   级数不足要拒绝——这些是纯逻辑，可以精确断言；
2. **真的会跑完并拿到逐级数值**：用 `test_part.step` 的粗网格跑三级
   （约 1.5 秒），断言单元数递增、量在变化、结论给出了话；
3. **不留痕迹**：临时几何与临时网格必须被删掉，且用户自己的
   `test_part.step.msh` 修改时间不变——这是前两轮踩过的坑；
4. **不越权下结论**：级数不足时必须返回 `insufficient` 而**不是**硬给一个
   收敛判定；结论里必须带上"自收敛不能证明模型正确"这句限定。

第 4 条是这一轮最重要的测试：这个功能最容易犯的错，是拿"两次数值接近"
当成"结果可信"。
"""

import asyncio
import os
import unittest

from fastapi import HTTPException

import convergence_study as study_module
from config import resolve_upload_path
from convergence_study import (
    MAX_STUDY_ELEMENTS,
    MIN_STUDY_LEVELS,
    StudyLevel,
    StudyRequest,
    derive_mesh_sizes,
    extract_quantities,
    study_impl,
)

CUBE = "test_part.step"


def _base_setup(face_id: int = 1, pull_id: int = 8) -> dict:
    """与 verify 里同一套配置：-x 面固定，+x 面拉 1000 N。"""
    return {
        "geometry_filename": CUBE,
        "material_id": "structural_steel",
        "boundary_conditions": [
            {"id": "fix", "name": "fixed", "type": "fixed",
             "applicationType": "face", "entityIndex": face_id},
            {"id": "pull", "name": "pull", "type": "force",
             "applicationType": "face", "entityIndex": pull_id,
             "force": {"x": 1000.0, "y": 0.0, "z": 0.0}},
        ],
        "length_unit": "mm",
    }


class DeriveMeshSizesTest(unittest.TestCase):
    """纯逻辑：网格尺寸序列怎么来、什么输入必须被拒绝。"""

    def test_default_halves_each_level(self):
        request = StudyRequest(setup=_base_setup(), base_mesh_size=4.0, levels=3)
        self.assertEqual(derive_mesh_sizes(request), [4.0, 2.0, 1.0])

    def test_four_levels(self):
        request = StudyRequest(setup=_base_setup(), base_mesh_size=8.0, levels=4)
        self.assertEqual(derive_mesh_sizes(request), [8.0, 4.0, 2.0, 1.0])

    def test_explicit_sizes_are_kept(self):
        request = StudyRequest(setup=_base_setup(), mesh_sizes=[3.0, 1.5, 0.75])
        self.assertEqual(derive_mesh_sizes(request), [3.0, 1.5, 0.75])

    def test_too_few_levels_is_rejected(self):
        request = StudyRequest(setup=_base_setup(), mesh_sizes=[3.0, 1.5])
        with self.assertRaises(HTTPException) as ctx:
            derive_mesh_sizes(request)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("3 级", ctx.exception.detail)

    def test_too_many_levels_is_rejected(self):
        """
        上限 4 级不是偷懒：单元数按 h³ 涨，5 级意味着最细一级是首级的 4096 倍。
        宁可明确拒绝，也不要让服务在几分钟后被内存打死。
        """
        request = StudyRequest(
            setup=_base_setup(), mesh_sizes=[16.0, 8.0, 4.0, 2.0, 1.0]
        )
        with self.assertRaises(HTTPException) as ctx:
            derive_mesh_sizes(request)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("最多", ctx.exception.detail)

    def test_non_decreasing_sizes_are_rejected(self):
        """加密必须是**严格**递减的：相等的一级等于什么都没做，却照样花时间。"""
        request = StudyRequest(setup=_base_setup(), mesh_sizes=[3.0, 3.0, 1.5])
        with self.assertRaises(HTTPException) as ctx:
            derive_mesh_sizes(request)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("递减", ctx.exception.detail)

    def test_non_positive_sizes_are_rejected(self):
        request = StudyRequest(setup=_base_setup(), mesh_sizes=[3.0, 1.5, 0.0])
        with self.assertRaises(HTTPException) as ctx:
            derive_mesh_sizes(request)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_levels_out_of_range_is_rejected(self):
        with self.assertRaises(HTTPException):
            derive_mesh_sizes(StudyRequest(setup=_base_setup(), levels=2))
        with self.assertRaises(HTTPException):
            derive_mesh_sizes(StudyRequest(setup=_base_setup(), levels=9))

    def test_unknown_field_is_rejected(self):
        """拼错的字段不能静默忽略——那会让人以为它生效了。"""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            StudyRequest(setup=_base_setup(), analyse_type="structural")


class ExtractQuantitiesTest(unittest.TestCase):
    """考察量的提取：必须是**标量**，且模态要跳过刚体模态。"""

    class _Structural:
        max_stress = 12.5
        max_displacement = 0.25

    class _Thermal:
        max_heat_flux = 500.0
        max_temperature = 373.15

    class _Modal:
        frequencies = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1234.5, 2345.6]
        rigid_body_modes = 6

    def test_structural(self):
        quantities = extract_quantities("structural", self._Structural())
        self.assertEqual(quantities["max_stress"], 12.5)
        self.assertEqual(quantities["max_displacement"], 0.25)

    def test_thermal(self):
        quantities = extract_quantities("thermal", self._Thermal())
        self.assertEqual(quantities["max_heat_flux"], 500.0)
        self.assertEqual(quantities["max_temperature"], 373.15)

    def test_modal_skips_rigid_body_modes(self):
        """
        自由-自由结构的前 6 阶频率恒为 0，且**与网格无关**——

        拿第 0 阶做收敛检查，会得到"完美收敛"（0 → 0 → 0），
        那是个假结论。必须取第一阶弹性频率。
        """
        quantities = extract_quantities("modal", self._Modal())
        self.assertEqual(quantities["first_elastic_frequency"], 1234.5)

    def test_modal_without_rigid_modes_takes_the_first(self):
        class Constrained:
            frequencies = [321.0, 654.0]
            rigid_body_modes = 0

        quantities = extract_quantities("modal", Constrained())
        self.assertEqual(quantities["first_elastic_frequency"], 321.0)

    def test_modal_with_index_beyond_range_is_clamped(self):
        """刚体模态数大于频率个数时不能越界（宁可取最后一阶也不要 500）。"""

        class Weird:
            frequencies = [10.0, 20.0]
            rigid_body_modes = 6

        quantities = extract_quantities("modal", Weird())
        self.assertEqual(quantities["first_elastic_frequency"], 20.0)

    def test_modal_without_frequencies_fails_loudly(self):
        class Empty:
            frequencies = []
            rigid_body_modes = 0

        with self.assertRaises(HTTPException) as ctx:
            extract_quantities("modal", Empty())
        self.assertEqual(ctx.exception.status_code, 500)


class ResponseBuildingTest(unittest.TestCase):
    """级数不足时**不许**下结论——这是本轮最重要的一条。"""

    def _levels(self, count: int, values=(100.0, 50.0, 25.0, 12.5)):
        """
        造 `count` 级数据。

        **必须把该分析类型声明的所有考察量都给全**：`_build_response` 会遍历
        `ANALYSES[type]["quantities"]` 逐个取值，缺一个就是 `KeyError`。
        这是刻意的——少一个考察量说明提取逻辑漏了，应当响亮地失败，
        而不是被默认值掩盖成"这个量不参与判定"。
        """
        return [
            StudyLevel(
                label=f"mesh_size={1.5 / 2 ** index:g}",
                mesh_size=1.5 / 2 ** index,
                elements=100 * 8 ** index,
                nodes=50 * 8 ** index,
                quantities={
                    "max_stress": values[index],
                    # 结构分析声明的第二个考察量；按同一比例缩下去，
                    # 保证它的收敛行为与 max_stress 一致
                    "max_displacement": values[index] * 1e-8,
                },
            )
            for index in range(count)
        ]

    def test_insufficient_levels_do_not_get_a_verdict(self):
        """
        只有 2 级时必须返回 `insufficient` 且 `assessments` 为空。

        两级结果接近可能是收敛，也可能是**两处都错得一样**（比如同一个
        错误的边界条件）。拿它下"已收敛"的结论是本功能最容易犯的错。
        """
        request = StudyRequest(setup=_base_setup(), levels=3)
        analysis = study_module.ANALYSES["structural"]
        response = study_module._build_response(
            request, analysis, self._levels(2), [], []
        )
        self.assertEqual(response.status, "insufficient")
        self.assertEqual(response.assessments, {})
        self.assertIn("无法判断", response.verdict)
        # 也要说清楚为什么不能判断
        self.assertTrue(any("少于" in note for note in response.notes))

    def test_converged_series_is_reported_with_the_caveat(self):
        """
        一条**真的收敛到极限**的序列：`u_k = 100 − 10·h_k²`（h 逐级减半）。

        差值 7.5 → 1.875 → 0.46875（比值 4 ⇒ 阶数 2），最后一级相对变化
        0.47% < 5% ⇒ `converged`。注意：一条收敛序列的"最后一级变化"大致
        就等于剩余误差，所以它不会很小——这也是默认阈值取 5% 而不是 2%
        的原因（见模块里 DEFAULT_TOLERANCE 的注释）。
        """
        request = StudyRequest(setup=_base_setup(), tolerance=0.05)
        analysis = study_module.ANALYSES["structural"]
        response = study_module._build_response(
            request,
            analysis,
            self._levels(4, values=(90.0, 97.5, 99.375, 99.84375)),
            [],
            [],
        )
        self.assertEqual(response.status, "converged")
        self.assertTrue(response.assessments["max_stress"]["converged"])
        self.assertAlmostEqual(
            response.assessments["max_stress"]["observed_order"], 2.0, places=9
        )
        # 必须一直带着"自收敛不能证明模型正确"这句限定
        self.assertIn("不代表模型", response.verdict)
        # 注意：notes 是 study_impl 组装并传进来的，这里传的是空列表，
        # 所以"限定说明"的断言放在 RealStudyTest.test_notes_carry_the_honest_limits
        # （那里跑的是真实调用链）。

    def test_still_changing_series_is_marginal(self):
        """
        都在趋稳、但变化幅度还大于阈值 ⇒ `marginal`，不是 `converged`。

        (100, 50, 25, 12.75) 是标准的逐级减半序列：差值在缩小（判为 converged
        没问题），但最后一级相对变化 96%——绝不能说"够细了"。
        """
        request = StudyRequest(setup=_base_setup(), tolerance=0.05)
        analysis = study_module.ANALYSES["structural"]
        response = study_module._build_response(
            request, analysis, self._levels(4, values=(100.0, 50.0, 25.0, 12.75)), [], []
        )
        self.assertEqual(response.status, "marginal")
        self.assertTrue(response.assessments["max_stress"]["converged"])
        self.assertIn("继续加密", response.verdict)

    def test_non_converging_series_points_at_singularity(self):
        """
        差值不缩小 ⇒ `not-converged`，而且必须**提到应力奇异**。

        尖角/点载荷处的真值本身无穷大，用户若无止境加密是浪费时间。
        """
        request = StudyRequest(setup=_base_setup())
        analysis = study_module.ANALYSES["structural"]
        response = study_module._build_response(
            request, analysis, self._levels(3, values=(1.0, 3.0, 8.0)), [], []
        )
        self.assertEqual(response.status, "not-converged")
        self.assertIn("应力奇异", response.verdict)

    def test_primary_quantity_drives_the_verdict_label(self):
        request = StudyRequest(setup=_base_setup())
        analysis = study_module.ANALYSES["structural"]
        response = study_module._build_response(
            request, analysis, self._levels(3), [], []
        )
        self.assertEqual(response.primary, "max_stress")
        self.assertEqual(response.labels["max_stress"], "最大 von Mises 应力")


class RealStudyTest(unittest.TestCase):
    """
    真的跑一遍（粗网格三级，约 1.5 秒）。

    覆盖的是"能不能跑通并拿到有用的东西"，不断言具体数值——那些取决于
    几何与网格，钉死了反而会变成脆弱的测试。
    """

    @classmethod
    def setUpClass(cls):
        cls.request = StudyRequest(
            setup=_base_setup(), analysis_type="structural",
            levels=3, base_mesh_size=3.0,
        )
        cls.result = asyncio.run(study_impl(cls.request))

    def test_three_levels_with_increasing_element_counts(self):
        self.assertEqual(len(self.result.levels), 3)
        counts = [level.elements for level in self.result.levels]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_mesh_sizes_halve(self):
        self.assertEqual(
            [level.mesh_size for level in self.result.levels], [3.0, 1.5, 0.75]
        )

    def test_both_quantities_are_present_and_positive(self):
        for level in self.result.levels:
            self.assertGreater(level.quantities["max_stress"], 0.0)
            self.assertGreater(level.quantities["max_displacement"], 0.0)

    def test_response_carries_assessments_and_a_verdict(self):
        self.assertIn(self.result.status, ("converged", "marginal", "not-converged"))
        self.assertIn("max_stress", self.result.assessments)
        self.assertIn("max_displacement", self.result.assessments)
        self.assertTrue(self.result.verdict)
        # 每个考察量都必须是"逐级数值都在收敛判定里出现过"
        for name, assessment in self.result.assessments.items():
            self.assertEqual(len(assessment["values"]), 3, name)

    def test_notes_carry_the_honest_limits(self):
        joined = " ".join(self.result.notes)
        self.assertIn("自收敛", joined)
        self.assertIn("应力奇异", joined)
        self.assertIn("临时副本", joined)
        self.assertIn("没有理论参照", joined)
        # 实测加密比必须报出来：gmsh 的 mesh_size 只是名义目标，真实网格并不
        # 按它成比例加密（实测 1.47/1.91…）。不报出来，用户看到"差值没变小"
        # 只会以为自己的模型有问题。
        self.assertIn("实际加密比", joined)
        self.assertIn("单元数", joined)

    def test_generalized_estimator_is_used(self):
        """
        用户模型的收敛阶必须按**实测**加密比估计。

        名义 mesh_size 是 2 倍递减，但实测单元数并不是 8 倍递增，
        套 `log(r)/log(2)` 会算出错的阶数。
        """
        for name, assessment in self.result.assessments.items():
            self.assertEqual(assessment["order_estimator"], "generalized", name)

    def test_temporary_files_are_cleaned_up(self):
        """
        临时几何与临时网格必须删干净。

        留着的话会在 `git status` 里变成未跟踪文件（上一轮
        `POST /api/generate-cube` 就是这么把工作区弄脏的）。
        """
        from config import UPLOAD_DIR

        leftovers = [
            path.name
            for path in UPLOAD_DIR.glob("conv_*")
        ]
        self.assertEqual(leftovers, [], f"临时文件残留：{leftovers}")

    def test_user_mesh_is_not_disturbed(self):
        """
        检查跑的是几何副本，不能覆盖用户自己的 `<几何名>.msh`。

        做法：**先确保用户有一份网格**（`*.msh` 是 gitignore 的可再生产物，
        干净检出里没有），记下修改时间与大小，再跑一次检查，比对。

        第一版少了"先生成"这一步：本地跑得过去（缓存早就有了），但 CI 的干净
        检出里必然 `FileNotFoundError`。**这就是"只在本机验证"的典型漏网方式**
        ——测试依赖了一个不进版本库的生成物。
        """
        from geometry import generate_mesh_impl

        target = str(resolve_upload_path(CUBE)) + ".msh"
        if not os.path.exists(target):
            asyncio.run(generate_mesh_impl(filename=CUBE, mesh_size=3.0))
        self.assertTrue(os.path.exists(target), f"夹具网格没生成出来：{target}")

        before = os.stat(target)
        asyncio.run(study_impl(self.request))
        after = os.stat(target)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_configured_service_is_not_disturbed(self):
        """几何文件本身也必须原样不动（副本改了不影响它）。"""
        target = resolve_upload_path(CUBE)
        before = os.stat(target)
        asyncio.run(study_impl(self.request))
        after = os.stat(target)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)


class StudyValidationTest(unittest.TestCase):
    """配置错误必须在**划网格之前**就被拒（否则用户等几分钟换来一句 400）。"""

    def test_unknown_analysis_type_is_rejected(self):
        request = StudyRequest(setup=_base_setup(), analysis_type="cfd")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(study_impl(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("analysis_type", ctx.exception.detail)

    def test_bad_setup_is_rejected_before_meshing(self):
        broken = _base_setup()
        broken["material_id"] = None          # 材料必须存在
        request = StudyRequest(setup=broken)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(study_impl(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("求解配置不合法", ctx.exception.detail)

    def test_missing_geometry_filename_is_rejected(self):
        setup = _base_setup()
        setup.pop("geometry_filename")
        request = StudyRequest(setup=setup)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(study_impl(request))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("geometry_filename", ctx.exception.detail)

    def test_unknown_geometry_is_404(self):
        setup = _base_setup()
        setup["geometry_filename"] = "definitely_missing_part.step"
        request = StudyRequest(setup=setup)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(study_impl(request))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_illegal_filename_is_400(self):
        setup = _base_setup()
        setup["geometry_filename"] = "../../etc/passwd.step"
        request = StudyRequest(setup=setup)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(study_impl(request))
        self.assertEqual(ctx.exception.status_code, 400)


class ElementCapTest(unittest.TestCase):
    """单元数上限：必须在**划下一级之前**就预判并停下，而不是划完才发现。"""

    def test_prediction_stops_before_an_oversized_level(self):
        """
        用一个极小上限把第一级之后的路堵死，断言只跑了一级就停下。

        这里直接改模块常量（测试完恢复）：上限是"运行期策略"，不是接口参数。
        """
        original = study_module.MAX_STUDY_ELEMENTS
        study_module.MAX_STUDY_ELEMENTS = 1
        try:
            request = StudyRequest(
                setup=_base_setup(), levels=3, base_mesh_size=3.0
            )
            result = asyncio.run(study_impl(request))
        finally:
            study_module.MAX_STUDY_ELEMENTS = original

        # 第一级就超上限 ⇒ 一级都没有，且必须给出"级数不足"而不是硬判
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(result.levels), 0)
        self.assertTrue(any("超过上限" in note for note in result.notes))

    def test_cap_constant_is_reasonable(self):
        self.assertGreater(MAX_STUDY_ELEMENTS, 10_000)
        self.assertLessEqual(MIN_STUDY_LEVELS, 3)


if __name__ == "__main__":
    unittest.main()
