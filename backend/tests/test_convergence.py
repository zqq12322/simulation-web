"""
收敛性检查的测试。

分三层，各自的"证据强度"不同，这也是刻意的：

1. **纯函数的解析对照**：给一串已知 `u_k = u* + C·h^p` 的合成序列，要求
   `observed_order` 把 `p` **精确**恢复出来，`richardson_limit` 把 `u*`
   精确恢复出来。这是闭式解，容差可以开到 1e-12。
2. **结构化网格上的制造解**：网格自相似 ⇒ 误差比值**恰好**是 `2^p`。
   实测 L2 阶 1.99999999999998、H1 阶 1.00000000000000，因此断言可以写得很紧
   （不是"大概二阶"，而是"就是二阶"）。
3. **真实网格流水线**：gmsh 划分 → `load_tet_mesh_from_msh` 读回。非结构网格
   在前渐近区比值会偏小，所以这里断言的是**方向性**（误差随加密单调下降、
   阶数为正且趋近理论值），而不是精确比值——那需要 4 级网格、约 7 秒，
   留给端到端 `verify` 去跑。

第 2 层与第 3 层的差别本身就是一条结论：**离散格式的阶数**和**我们自己的
网格读写有没有破坏它**是两个问题，必须分别测。
"""

import asyncio
import unittest

import numpy as np

import convergence as convergence_module
from convergence import (
    PIPELINE_MESH_SIZES,
    STRUCTURED_LEVELS,
    THEORY_H1_ORDER,
    THEORY_L2_ORDER,
    assess,
    exact_gradient,
    exact_temperature,
    generalized_order,
    is_monotone,
    measure_errors,
    observed_order,
    pipeline_levels,
    richardson_limit,
    structured_levels,
    successive_differences,
)


class SyntheticSequenceTest(unittest.TestCase):
    """第 1 层：纯函数对合成序列的行为（全部有闭式答案）。"""

    def test_successive_differences(self):
        self.assertEqual(successive_differences([1.0, 3.0, 6.0]), [2.0, 3.0])
        self.assertEqual(successive_differences([5.0]), [])
        # 用绝对值：加密过程中数值可能从上方或下方趋近
        self.assertEqual(successive_differences([3.0, 1.0]), [2.0])

    def test_monotone_detection(self):
        self.assertTrue(is_monotone([1.0, 2.0, 3.0]))
        self.assertTrue(is_monotone([3.0, 2.0, 1.0]))
        self.assertTrue(is_monotone([1.0, 1.0, 1.0]))
        self.assertTrue(is_monotone([1.0]))
        self.assertFalse(is_monotone([1.0, 3.0, 2.0]))
        # 微小但方向一致的下降仍然是单调的（收敛序列的典型形态）
        self.assertTrue(is_monotone([1.0, 1.0 - 1e-9, 1.0 - 1e-9 - 1e-12]))

    def test_observed_order_recovers_second_order_exactly(self):
        """
        `u_k = u* + C·h_k^p`，h 减半 ⇒ 差值比值恰好 `2^p`。

        用 p = 2、C = 3、u* = 7 造序列，要求**精确**恢复 p = 2。
        """
        exact = 7.0
        h = np.array([0.4, 0.2, 0.1])
        values = exact + 3.0 * h ** 2
        self.assertAlmostEqual(observed_order(values, ratio=2.0), 2.0, places=12)

    def test_observed_order_recovers_first_order_exactly(self):
        h = np.array([0.4, 0.2, 0.1])
        values = 1.5 - 2.0 * h
        self.assertAlmostEqual(observed_order(values, ratio=2.0), 1.0, places=12)

    def test_observed_order_uses_the_last_three_values_only(self):
        """
        只取最后三个值：前几级还在前渐近区时，用它算阶数会被拖偏。

        构造的序列 [1000, 100, 10, 4, 1] 里，前两级差值（900、90）给出的
        阶数是 log(10)/log(2) ≈ 3.32，而末三级（10、4、1）给出的正好是 1.0。
        断言等于 1.0 就证明了"只看末三级"。
        """
        values = [1000.0, 100.0, 10.0, 4.0, 1.0]
        early = np.log(900.0 / 90.0) / np.log(2.0)
        self.assertAlmostEqual(early, 3.321928094887362, places=9)
        self.assertAlmostEqual(observed_order(values, ratio=2.0), 1.0, places=12)

    def test_observed_order_needs_three_levels(self):
        self.assertIsNone(observed_order([1.0, 0.5], ratio=2.0))
        self.assertIsNone(observed_order([], ratio=2.0))

    def test_observed_order_rejects_non_decreasing_differences(self):
        """
        后一段差值没变小 ⇒ 返回 None，而不是报一个 0 或负数。

        报负数会让人以为"阶数是负的"，而真实含义是"还没进入收敛区"。
        """
        self.assertIsNone(observed_order([1.0, 0.0, -2.0], ratio=2.0))
        # 差值相等 ⇒ 阶数为 0，同样视为"给不出阶数"
        self.assertIsNone(observed_order([3.0, 2.0, 1.0], ratio=2.0))

    def test_observed_order_rejects_zero_differences(self):
        self.assertIsNone(observed_order([2.0, 2.0, 1.0], ratio=2.0))
        self.assertIsNone(observed_order([2.0, 1.0, 1.0], ratio=2.0))

    def test_richardson_limit_recovers_the_exact_value(self):
        """同样用闭式序列：外推出来的极限必须就是 `u*`。"""
        exact = -4.25
        h = np.array([0.2, 0.1, 0.05])
        values = exact + 8.0 * h ** 1.5
        order = observed_order(values, ratio=2.0)
        self.assertAlmostEqual(order, 1.5, places=12)
        self.assertAlmostEqual(
            richardson_limit(values, order, ratio=2.0), exact, places=10
        )

    def test_richardson_limit_needs_an_order(self):
        self.assertIsNone(richardson_limit([1.0, 0.5, 0.25], None))
        self.assertIsNone(richardson_limit([1.0, 0.5, 0.25], 0.0))
        self.assertIsNone(richardson_limit([1.0, 0.5, 0.25], -1.0))
        self.assertIsNone(richardson_limit([1.0], 2.0))


class AssessTest(unittest.TestCase):
    """第 1 层续：结论判定本身必须"该通过时通过、该拒绝时拒绝"。"""

    def test_accepts_a_second_order_sequence(self):
        h = np.array([0.4, 0.2, 0.1])
        result = assess(5.0 + 2.0 * h ** 2, expected_order=2.0, label="测试量")
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["observed_order"], 2.0, places=12)
        self.assertAlmostEqual(result["extrapolated_limit"], 5.0, places=10)
        self.assertIn("2.00", result["verdict"])

    def test_rejects_a_sequence_that_is_not_converging(self):
        """差值不变小 ⇒ 必须判为"尚未收敛"，而不是给一个 0 阶数。"""
        result = assess([3.0, 2.0, 1.0, 0.0], expected_order=1.0, label="测试量")
        self.assertFalse(result["converged"])
        self.assertIsNone(result["observed_order"])
        self.assertIn("没有变小", result["verdict"])

    def test_rejects_a_non_monotone_sequence(self):
        result = assess([1.0, 3.0, 2.0, 4.0], label="测试量")
        self.assertFalse(result["converged"])
        self.assertFalse(result["monotone"])
        self.assertIn("跳动", result["verdict"])

    def test_rejects_a_wrong_order(self):
        """
        数值在变、但**不是按理论阶数**变——必须单独报出来。

        这种情况（例如一阶格式却期望二阶）和"网格不够细"的处理方式完全不同，
        所以措辞必须分开。
        """
        h = np.array([0.4, 0.2, 0.1])
        result = assess(1.0 + 3.0 * h, expected_order=2.0, label="测试量")
        self.assertFalse(result["converged"])
        self.assertAlmostEqual(result["observed_order"], 1.0, places=12)
        self.assertIn("相差过大", result["verdict"])

    def test_two_levels_are_not_enough(self):
        result = assess([9.0, 3.0], label="测试量")
        self.assertFalse(result["converged"])
        self.assertIsNone(result["observed_order"])
        self.assertIn("3 级", result["verdict"])

    def test_machine_precision_is_treated_as_converged(self):
        """最后两级完全相同：比收敛更好，但要说明"阶数无从谈起"。"""
        result = assess([2.0, 1.0, 1.0], label="测试量")
        self.assertTrue(result["converged"])
        self.assertIsNone(result["observed_order"])
        self.assertIn("机器精度", result["verdict"])

    def test_relative_change_is_reported(self):
        """只报绝对差不够：同样差 0.1，量级是 1 还是 1e6 含义完全不同。"""
        result = assess([1.0, 0.5, 0.25], ratio=2.0, label="测试量")
        # 最后一级的绝对差是 0.25，而整个量级也是 0.25 ⇒ 相对变化 100%
        self.assertAlmostEqual(result["last_relative_change"], 1.0, places=12)

        # 绝对差 10（比上面大 40 倍），但相对变化小四个数量级
        large = assess([1e5, 1e5 - 10.0, 1e5 - 20.0], ratio=2.0, label="测试量")
        self.assertLess(large["last_relative_change"], 2e-4)
        self.assertGreater(
            result["last_relative_change"], 1000 * large["last_relative_change"]
        )

    def test_honours_a_non_halving_ratio(self):
        """加密比不一定是 2：这里按 4 倍加密，阶数必须按 log(4) 算。"""
        h = np.array([0.4, 0.1, 0.025])
        values = 2.0 + 3.0 * h ** 2
        result = assess(values, ratio=4.0, expected_order=2.0, label="测试量")
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["observed_order"], 2.0, places=12)


class GeneralizedOrderTest(unittest.TestCase):
    """
    广义 Richardson 阶数：用**实测**网格尺寸估阶。

    为什么必须单独测：真实网格（gmsh 划的非结构网格）不会按名义尺寸成比例
    加密——实测 `test_part.step` 用 3/1.5/0.75 划出来的单元数是 426/1366/9462，
    折算加密比是 1.47 和 1.91。在这种数据上套 `log(r)/log(2)` 会算出错的阶数。
    """

    @staticmethod
    def _sequence(order: float, sizes, exact: float = 0.0, constant: float = 1.0):
        """`u_k = u* + C·h_k^p`（误差按 h^p 衰减的合成序列）。"""
        return [exact + constant * size ** order for size in sizes]

    def test_recovers_the_order_with_uniform_sizes(self):
        sizes = [1.0, 0.5, 0.25]
        values = self._sequence(2.0, sizes)
        self.assertAlmostEqual(generalized_order(values, sizes), 2.0, places=9)

    def test_recovers_the_order_with_non_uniform_sizes(self):
        """
        非均匀加密也必须能恢复正确阶数——这正是它存在的理由。

        用 1 / 0.6 / 0.3 造 p = 2 的序列；若错误地按"每级减半"算，
        会得到 log(r)/log(2) ≈ 2.245 而不是 2。
        """
        sizes = [1.0, 0.6, 0.3]
        values = self._sequence(2.0, sizes)
        self.assertAlmostEqual(generalized_order(values, sizes), 2.0, places=9)
        # 对照：按均匀比估计会偏掉，说明这个函数不是多余的
        naive = observed_order(values, ratio=2.0)
        self.assertGreater(abs(naive - 2.0), 0.2)

    def test_recovers_first_order_with_non_uniform_sizes(self):
        sizes = [0.5, 0.2, 0.05]
        values = self._sequence(1.0, sizes)
        self.assertAlmostEqual(generalized_order(values, sizes), 1.0, places=9)

    def test_is_scale_invariant(self):
        """只用尺寸的比值，所以整体缩放网格不影响结果。"""
        sizes = [1.0, 0.6, 0.3]
        values = self._sequence(1.5, sizes)
        scaled = [size * 1000.0 for size in sizes]
        self.assertAlmostEqual(
            generalized_order(values, sizes),
            generalized_order(values, scaled),
            places=12,
        )

    def test_growing_differences_have_no_positive_order(self):
        """
        差值**变大**且变大的幅度超出网格加密所能解释的范围 ⇒ 返回 None。

        返回 None 而不是负数：负的"阶数"没有物理含义，真实结论是
        "没有任何正的收敛阶能解释这组数据"。
        """
        sizes = [1.0, 0.9, 0.8]          # 加密幅度很小
        values = [1.0, 0.5, -2.0]        # 差值却在变大
        self.assertIsNone(generalized_order(values, sizes))

    def test_needs_matching_lengths_and_three_levels(self):
        self.assertIsNone(generalized_order([1.0, 0.5], [1.0, 0.5]))
        self.assertIsNone(generalized_order([1.0, 0.5, 0.25], [1.0, 0.5]))
        self.assertIsNone(generalized_order([], []))

    def test_rejects_non_decreasing_sizes(self):
        self.assertIsNone(generalized_order([1.0, 0.5, 0.25], [1.0, 0.5, 0.5]))
        self.assertIsNone(generalized_order([1.0, 0.5, 0.25], [0.25, 0.5, 1.0]))

    def test_zero_differences_are_rejected(self):
        self.assertIsNone(generalized_order([1.0, 1.0, 0.9], [1.0, 0.5, 0.25]))
        self.assertIsNone(generalized_order([1.0, 0.9, 0.9], [1.0, 0.5, 0.25]))

    def test_assess_prefers_the_generalized_estimator_when_sizes_given(self):
        """`assess(sizes=...)` 必须真的改用它，并在结果里标明用了哪一个。"""
        sizes = [1.0, 0.6, 0.3]
        values = self._sequence(2.0, sizes)
        result = assess(values, sizes=sizes, label="测试量")
        self.assertEqual(result["order_estimator"], "generalized")
        self.assertAlmostEqual(result["observed_order"], 2.0, places=9)

        without = assess(values, ratio=2.0, label="测试量")
        self.assertEqual(without["order_estimator"], "uniform-ratio")

    def test_extrapolation_uses_the_measured_local_ratio(self):
        """
        外推必须用最后两级的**真实**加密比。

        取 p = 1、尺寸 1 / 0.5 / 0.25（末两级比 2）时极限应等于 u*；
        取 1 / 0.6 / 0.3（末两级比 2）同理，但若误用 2.0 而不是实际比，
        结果会偏。这里用 p = 2、末两级比 2 与 3 各验一次。
        """
        for sizes, ratio_last in (([1.0, 0.5, 0.25], 2.0), ([1.0, 0.6, 0.2], 3.0)):
            values = self._sequence(2.0, sizes, exact=5.0)
            result = assess(values, sizes=sizes, expected_order=2.0, label="测试量")
            self.assertAlmostEqual(result["refinement_ratio"], ratio_last, places=9)
            self.assertAlmostEqual(result["extrapolated_limit"], 5.0, places=6)

    """第 2 层：制造解本身与误差度量的自洽性。"""

    def test_exact_solution_is_harmonic(self):
        """
        `T = x² − y²` 必须是调和函数，否则它就不是 `∇·(k∇T)=0` 的解，
        整个基准的前提就没了。

        用差分数值验证拉普拉斯算子为零（而不是靠"我记得它是调和的"）。
        """
        step = 1e-4
        coords = np.array([[0.3], [0.7], [0.15]])
        laplacian = 0.0
        for axis in range(3):
            plus = coords.copy()
            minus = coords.copy()
            plus[axis] += step
            minus[axis] -= step
            laplacian += (
                exact_temperature(plus) - 2 * exact_temperature(coords)
                + exact_temperature(minus)
            ) / step ** 2
        # 用 .item() 取出标量：直接对 (1,) 数组调 assertAlmostEqual 会在 round() 上抛错
        self.assertAlmostEqual(laplacian.item(), 0.0, places=6)

    def test_exact_gradient_matches_finite_difference(self):
        coords = np.array([[0.3, 0.9], [0.7, 0.2], [0.15, 0.55]])
        step = 1e-6
        analytic = exact_gradient(coords)
        for axis in range(3):
            plus = coords.copy()
            minus = coords.copy()
            plus[axis] += step
            minus[axis] -= step
            numerical = (exact_temperature(plus) - exact_temperature(minus)) / (2 * step)
            np.testing.assert_allclose(analytic[axis], numerical, atol=1e-8)

    def test_error_is_not_machine_precision(self):
        """
        误差必须**明显大于**机器精度。

        如果误用了线性精确解（P1 空间里就有），误差会是 1e-16 量级，
        基准就变成了空转——所有网格都"收敛"，什么都测不出来。这条断言
        把这个陷阱钉死。
        """
        coordinates = np.linspace(0.0, 1.0, 5)
        from skfem import MeshTet

        errors = measure_errors(MeshTet.init_tensor(coordinates, coordinates, coordinates))
        self.assertGreater(errors["l2"], 1e-4)
        self.assertGreater(errors["h1"], 1e-3)
        self.assertLess(errors["l2"], 1.0)
        self.assertEqual(errors["elements"], 6 * 4 ** 3)
        self.assertEqual(errors["nodes"], 5 ** 3)


class StructuredConvergenceTest(unittest.TestCase):
    """第 2 层：结构化网格上的实测阶数（比值恰好是 2^p）。"""

    @classmethod
    def setUpClass(cls):
        # n = 4, 8, 16：约 0.2 秒。第 4 级（n=32，19.6 万单元）留给 verify。
        cls.levels = structured_levels(STRUCTURED_LEVELS[:3])
        cls.l2 = [level["l2"] for level in cls.levels]
        cls.h1 = [level["h1"] for level in cls.levels]

    def test_mesh_sizes_and_element_counts(self):
        self.assertEqual([level["h"] for level in self.levels], [0.25, 0.125, 0.0625])
        self.assertEqual([level["elements"] for level in self.levels], [384, 3072, 24576])

    def test_l2_error_halves_by_exactly_four(self):
        """
        L2 误差按 `h²` 下降 ⇒ 网格减半，误差变成 1/4。

        断言写得很紧（比值与 4 的相对偏差 < 1e-9），因为结构化网格自相似、
        没有任何前渐近噪声——实测比值就是 4.000000000000000。
        """
        for earlier, later in zip(self.l2, self.l2[1:]):
            self.assertAlmostEqual(earlier / later, 4.0, places=9)

    def test_h1_error_halves_by_exactly_two(self):
        for earlier, later in zip(self.h1, self.h1[1:]):
            self.assertAlmostEqual(earlier / later, 2.0, places=9)

    def test_observed_orders_match_theory(self):
        l2_order = observed_order(self.l2, ratio=2.0)
        h1_order = observed_order(self.h1, ratio=2.0)
        self.assertAlmostEqual(l2_order, THEORY_L2_ORDER, places=9)
        self.assertAlmostEqual(h1_order, THEORY_H1_ORDER, places=9)

    def test_assessment_passes_and_extrapolates_to_zero(self):
        """
        考察量是**误差**，所以外推极限必须趋于 0（真值是精确解）。

        这比"阶数对了"更强：它说明误差不仅在按比例缩小，而且缩小的方向
        是对的（朝真值，而不是朝另一个错误的稳定值）。
        """
        result = assess(self.l2, expected_order=THEORY_L2_ORDER, label="L2 误差")
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["extrapolated_limit"], 0.0, places=12)
        h1_result = assess(self.h1, expected_order=THEORY_H1_ORDER, label="H1 误差")
        self.assertTrue(h1_result["converged"])
        self.assertAlmostEqual(h1_result["extrapolated_limit"], 0.0, places=12)

    def test_error_decreases_monotonically(self):
        for values in (self.l2, self.h1):
            self.assertTrue(is_monotone(values))
            differences = successive_differences(values)
            for earlier, later in zip(differences, differences[1:]):
                self.assertLess(later, earlier)


class PipelineConvergenceTest(unittest.TestCase):
    """
    第 3 层：走真实网格流水线（gmsh → `load_tet_mesh_from_msh`）。

    这里刻意用**较粗的三级**（约 0.7 秒）而不是完整四级（约 7 秒）：单测要跑得勤，
    而完整序列的阶数判定放在端到端 `verify` 里（那里才需要"数字好看"）。
    因此这里断言的是方向性——单调、阶数为正、且 L2 阶明显高于 H1 阶
    （二阶 vs 一阶这个**定性关系**即使在粗网格上也必须成立）。
    """

    coarsest = PIPELINE_MESH_SIZES[:3]

    @classmethod
    def setUpClass(cls):
        cls.levels = asyncio.run(pipeline_levels(cls.coarsest))
        cls.l2 = [level["l2"] for level in cls.levels]
        cls.h1 = [level["h1"] for level in cls.levels]

    def test_three_levels_are_returned(self):
        self.assertEqual(len(self.levels), 3)
        self.assertEqual(
            [level["h"] for level in self.levels],
            [2.5, 1.25, 0.625],
        )

    def test_errors_decrease_with_refinement(self):
        for values in (self.l2, self.h1):
            for earlier, later in zip(values, values[1:]):
                self.assertLess(later, earlier, values)

    def test_orders_are_positive_and_correctly_ranked(self):
        """
        粗网格上阶数不精确（前渐近），但**必须为正**，而且 L2 阶要高于 H1 阶。

        如果网格读写把朝向或节点映射弄错了，误差会乱掉、阶数会变成负数或
        完全随机——那时这两条会同时失败。
        """
        l2_order = observed_order(self.l2, ratio=2.0)
        h1_order = observed_order(self.h1, ratio=2.0)
        self.assertIsNotNone(l2_order)
        self.assertIsNotNone(h1_order)
        self.assertGreater(l2_order, 1.0, f"L2 阶 {l2_order} 偏低")
        self.assertGreater(h1_order, 0.4, f"H1 阶 {h1_order} 偏低")
        self.assertGreater(l2_order, h1_order)

    def test_temporary_geometry_is_cleaned_up(self):
        """
        基准用完必须删掉临时几何。

        上一轮已经因为"为了验证而调用的接口改了别的东西"吃过一次亏：
        `POST /api/generate-cube` 会重写被 git 跟踪的 STEP 文件。这里如果
        留下 `benchmark_cube.step`，它会在 `git status` 里变成未跟踪文件，
        最后被误提交。
        """
        import os

        from config import resolve_upload_path

        target = resolve_upload_path(convergence_module.BENCHMARK_GEOMETRY)
        self.assertFalse(os.path.exists(target), f"临时几何残留：{target}")
        self.assertFalse(
            os.path.exists(str(target) + ".msh"), f"临时网格残留：{target}.msh"
        )

    def test_benchmark_does_not_disturb_default_cube_mesh(self):
        """
        基准划分的是副本，不能覆盖用户正在用的 `default_cube.step.msh`。

        做法：先记下该文件的修改时间与大小，跑一次基准，再比对。
        """
        import os

        from config import resolve_upload_path

        target = str(resolve_upload_path("default_cube.step")) + ".msh"
        existed = os.path.exists(target)
        before = os.stat(target) if existed else None

        asyncio.run(pipeline_levels(self.coarsest))

        if not existed:
            # 本来就没有这个网格，那基准也不该顺手给用户造一个
            self.assertFalse(os.path.exists(target), "基准不该创建 default_cube 的网格")
            return
        after = os.stat(target)
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)


class BenchmarkEndpointTest(unittest.TestCase):
    """端点层：直接调用实现函数，检查响应结构（不启服务）。"""

    def test_structured_only_response(self):
        from fastapi import HTTPException

        from convergence import BenchmarkRequest, convergence_benchmark

        try:
            result = asyncio.run(
                convergence_benchmark(BenchmarkRequest(mode="structured", levels=3))
            )
        except HTTPException as exc:  # pragma: no cover - 失败时给出可读信息
            self.fail(f"基准返回 HTTP {exc.status_code}：{exc.detail}")

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.levels), 3)
        self.assertEqual(result.l2["observed_order"] is not None, True)
        self.assertAlmostEqual(result.l2["observed_order"], 2.0, places=6)
        self.assertAlmostEqual(result.h1["observed_order"], 1.0, places=6)
        self.assertTrue(result.verdict)
        # 结论里必须点明"这是判定用户模型够不够细的前提"
        self.assertIn("前提", result.verdict)
        # 也必须写清楚"边界条件没走生产路径"这个边界
        self.assertTrue(any("面→节点" in note for note in result.notes))

    def test_invalid_mode_is_rejected(self):
        from fastapi import HTTPException

        from convergence import BenchmarkRequest, convergence_benchmark

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(convergence_benchmark(BenchmarkRequest(mode="nope")))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_levels_out_of_range_is_rejected_by_the_model(self):
        from pydantic import ValidationError

        from convergence import BenchmarkRequest

        with self.assertRaises(ValidationError):
            BenchmarkRequest(levels=2)
        with self.assertRaises(ValidationError):
            BenchmarkRequest(levels=99)

    def test_unknown_field_is_rejected(self):
        """`extra="forbid"`：拼错的字段不能静默忽略（那会让人以为它生效了）。"""
        from pydantic import ValidationError

        from convergence import BenchmarkRequest

        with self.assertRaises(ValidationError):
            BenchmarkRequest(mode="structured", levles=3)


if __name__ == "__main__":
    unittest.main()
