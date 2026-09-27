"""
网格质量度量的测试。

核心断言全部来自**闭式解**，没有一条是"数值看起来合理"：

| 单元 | 形状质量 q 的解析值 |
|---|---|
| 正四面体（棱长 1） | **恰好 1** |
| 直角四面体（三条互相垂直的单位棱） | `(1/6)·6√2 ÷ (3/2)^{3/2} ≈ 0.7698` |
| 共面（退化） | **恰好 0** |
| 朝向反了（左手） | **负值**（必须能被单独数出来，而不是取绝对值掩盖） |
| 任意整体缩放 | **不变**（纯几何比值，与长度单位无关） |

这些是这套度量的价值所在：它把"网格好不好"变成了可被计算、可被对照的量。
只报"最小/最大棱长比"之类看着合理的指标是无法钉住的。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

import numpy as np

import mesh_quality as mesh_quality_module
from mesh_quality import (
    HISTOGRAM_BINS,
    POOR_QUALITY_THRESHOLD,
    build_histogram,
    edge_lengths,
    edge_ratios,
    shape_quality,
    summarise,
    tet_volumes,
)

# 正四面体：棱长 1
REGULAR_TET = np.array([
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.5, np.sqrt(3) / 2, 0.0],
    [0.5, np.sqrt(3) / 6, np.sqrt(2.0 / 3.0)],
], dtype=float).T

# 直角四面体：三条互相垂直的单位棱
RIGHT_TET = np.array([
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0],
], dtype=float).T

# 退化：四点共面
FLAT_TET = np.array([
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [1.0, 1.0, 0.0],
], dtype=float).T

# 刀片（sliver）：一个方向被压扁
SLIVER_TET = np.array([
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.5, 0.866, 0.0],
    [0.5, 0.288, 1e-4],
], dtype=float).T


def cells_of(*columns) -> np.ndarray:
    return np.asarray(columns, dtype=np.int64).T


class ShapeQualityAnalyticTest(unittest.TestCase):
    def test_regular_tetrahedron_is_exactly_one(self):
        """定义就是"与同棱长的正四面体之比"，所以正四面体必须恰好是 1。"""
        quality = shape_quality(REGULAR_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(quality[0]), 1.0, places=12)

    def test_regular_tetrahedron_volume(self):
        """正四面体体积 = a³/(6√2)。"""
        volume = tet_volumes(REGULAR_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(volume[0]), 1.0 / (6 * np.sqrt(2)), places=12)

    def test_right_corner_tetrahedron_matches_hand_calculation(self):
        """
        直角四面体（三条互相垂直的单位棱）的质量可手算：

            V = 1/6
            6 条棱：1,1,1,√2,√2,√2 ⇒ RMS² = (3·1 + 3·2)/6 = 3/2 ⇒ L = (3/2)^{1/2}
            q = V / (L³/(6√2)) = (1/6)·6√2 / (3/2)^{3/2} ≈ 0.7698
        """
        expected = (1.0 / 6.0) * 6 * np.sqrt(2) / (1.5 ** 1.5)
        quality = shape_quality(RIGHT_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(quality[0]), expected, places=12)
        self.assertAlmostEqual(expected, 0.769800358919501, places=12)

    def test_flat_tetrahedron_is_exactly_zero(self):
        quality = shape_quality(FLAT_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(quality[0]), 0.0, places=15)

    def test_sliver_is_much_worse_than_regular(self):
        quality = shape_quality(SLIVER_TET, cells_of([0, 1, 2, 3]))
        self.assertGreater(float(quality[0]), 0.0)
        self.assertLess(float(quality[0]), 0.01, "刀片单元应当远小于 1")

    def test_flipped_element_has_negative_quality(self):
        """
        朝向反了（雅可比为负）必须给出**负值**。

        取绝对值会把"朝向错误"伪装成"质量不错"——那是比形状差更严重的问题。
        """
        flipped = cells_of([0, 2, 1, 3])
        quality = shape_quality(REGULAR_TET, flipped)
        self.assertLess(float(quality[0]), 0.0)
        self.assertAlmostEqual(abs(float(quality[0])), 1.0, places=12)

    def test_quality_is_scale_invariant(self):
        """纯几何比值：整体缩放不改变质量（因此与长度单位 m/mm 无关）。"""
        scaled = REGULAR_TET * 1000.0
        self.assertAlmostEqual(
            float(shape_quality(scaled, cells_of([0, 1, 2, 3]))[0]),
            1.0, places=12,
        )
        self.assertAlmostEqual(
            float(shape_quality(RIGHT_TET * 0.001, cells_of([0, 1, 2, 3]))[0]),
            float(shape_quality(RIGHT_TET, cells_of([0, 1, 2, 3]))[0]),
            places=12,
        )

    def test_quality_never_exceeds_one(self):
        """
        q ≤ 1 是定义的性质（正四面体是给定棱长下体积最大的四面体）。

        这条断言能挡住"公式写反/漏了常数"这类错误——那类错误会让 q 大出好几倍。
        """
        points = np.concatenate([REGULAR_TET, RIGHT_TET + 5.0, SLIVER_TET + 9.0], axis=1)
        cells = cells_of([0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11])
        quality = shape_quality(points, cells)
        self.assertTrue(np.all(quality <= 1.0 + 1e-12), quality)


class EdgeMetricTest(unittest.TestCase):
    def test_regular_tet_edges_are_equal(self):
        ratios = edge_ratios(REGULAR_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(ratios[0]), 1.0, places=12)

    def test_right_tet_edge_ratio_is_sqrt_two(self):
        ratios = edge_ratios(RIGHT_TET, cells_of([0, 1, 2, 3]))
        self.assertAlmostEqual(float(ratios[0]), np.sqrt(2.0), places=12)

    def test_six_edges_are_returned(self):
        lengths = edge_lengths(RIGHT_TET, cells_of([0, 1, 2, 3]))
        self.assertEqual(lengths.shape, (1, 6))

    def test_flat_tet_edge_ratio_is_finite(self):
        ratios = edge_ratios(FLAT_TET, cells_of([0, 1, 2, 3]))
        self.assertTrue(np.isfinite(ratios[0]))


class SummariseTest(unittest.TestCase):
    def _mixed_mesh(self):
        """三个单元：正四面体、直角四面体、刀片。"""
        points = np.concatenate(
            [REGULAR_TET, RIGHT_TET + 5.0, SLIVER_TET + 9.0], axis=1
        )
        cells = cells_of([0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11])
        return points, cells

    def test_counts_and_extremes(self):
        points, cells = self._mixed_mesh()
        summary = summarise(points, cells)
        self.assertEqual(summary["elements"], 3)
        self.assertEqual(summary["nodes"], 12)
        self.assertAlmostEqual(summary["quality"]["max"], 1.0, places=12)
        self.assertLess(summary["quality"]["min"], 0.01)
        self.assertEqual(summary["non_positive_volume_count"], 0)
        self.assertEqual(summary["non_finite_count"], 0)

    def test_histogram_counts_sum_to_element_count(self):
        """
        直方图所有箱的计数之和必须等于单元总数。

        否则界面上看起来"少了一些单元"，而那正是最需要解释的部分
        （被丢弃的往往是畸形单元）。
        """
        points, cells = self._mixed_mesh()
        summary = summarise(points, cells)
        self.assertEqual(len(summary["histogram"]), HISTOGRAM_BINS)
        self.assertEqual(
            sum(bin_["count"] for bin_ in summary["histogram"]),
            summary["elements"],
        )

    def test_histogram_bins_cover_zero_to_one(self):
        histogram = build_histogram(np.array([0.0, 0.5, 1.0]))
        self.assertAlmostEqual(histogram[0]["lo"], 0.0)
        self.assertAlmostEqual(histogram[-1]["hi"], 1.0)
        # 相邻箱首尾相接，不留缝隙（否则会有值落不进任何箱）
        for earlier, later in zip(histogram, histogram[1:]):
            self.assertAlmostEqual(earlier["hi"], later["lo"])

    def test_histogram_counts_out_of_range_values(self):
        """越界值与负值必须被归入首尾箱，而不是被静默丢弃。"""
        histogram = build_histogram(np.array([-5.0, 0.5, 7.0]))
        self.assertEqual(sum(bin_["count"] for bin_ in histogram), 3)
        self.assertEqual(histogram[0]["count"], 1)    # -5 归入首箱
        self.assertEqual(histogram[-1]["count"], 1)   # 7 归入末箱

    def test_worst_elements_are_sorted_by_quality(self):
        points, cells = self._mixed_mesh()
        summary = summarise(points, cells)
        qualities = [item["quality"] for item in summary["worst_elements"]]
        self.assertEqual(qualities, sorted(qualities))
        self.assertLess(qualities[0], 0.01, "最差的那个应当是刀片单元")

    def test_non_positive_volume_is_counted_separately(self):
        """退化与朝向错误必须各算各的，不能被平均掉。"""
        points = np.concatenate([REGULAR_TET, FLAT_TET + 5.0], axis=1)
        cells = cells_of([0, 1, 2, 3], [4, 5, 6, 7])
        summary = summarise(points, cells)
        self.assertEqual(summary["non_positive_volume_count"], 1)

    def test_poor_count_uses_the_documented_threshold(self):
        points, cells = self._mixed_mesh()
        summary = summarise(points, cells)
        self.assertLess(summary["quality"]["min"], POOR_QUALITY_THRESHOLD)
        self.assertGreaterEqual(summary["poor_count"], 1)

    def test_empty_mesh_is_rejected_with_a_clear_message(self):
        with self.assertRaises(ValueError):
            summarise(REGULAR_TET, np.zeros((4, 0), dtype=np.int64))


class MeshQualityApiTest(unittest.TestCase):
    """端点层：直接调用实现函数，用真实的 default_cube 网格。"""

    CUBE = "default_cube.step"

    @classmethod
    def setUpClass(cls):
        from geometry import generate_mesh

        cls.mesh = asyncio.run(generate_mesh(cls.CUBE, 1.5))

    def test_quality_of_a_real_mesh(self):
        result = asyncio.run(mesh_quality_module.mesh_quality_impl(self.CUBE))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.elements, len(self.mesh.elements))
        self.assertEqual(result.nodes, len(self.mesh.nodes))
        # 立方体的网格不该有退化单元
        self.assertEqual(result.non_positive_volume_count, 0)
        self.assertEqual(result.non_finite_count, 0)
        # 形状质量必须落在 (0, 1]
        self.assertGreater(result.quality["min"], 0.0)
        self.assertLessEqual(result.quality["max"], 1.0 + 1e-12)
        self.assertGreater(result.quality["mean"], 0.1)

    def test_total_volume_matches_the_cube(self):
        """10×10×10 立方体（按默认单位 m）的总体积应精确等于 1000。"""
        result = asyncio.run(mesh_quality_module.mesh_quality_impl(self.CUBE))
        self.assertAlmostEqual(result.total_volume, 1000.0, places=6)

    def test_histogram_is_complete(self):
        result = asyncio.run(mesh_quality_module.mesh_quality_impl(self.CUBE))
        self.assertEqual(
            sum(bin_["count"] for bin_ in result.histogram), result.elements
        )

    def test_verdict_is_present_and_mentions_convergence(self):
        """
        结论里必须点出"形状好 ≠ 网格够细"。

        否则用户会把"质量直方图好看"当成"结果可信"，而那是两件事。
        """
        result = asyncio.run(mesh_quality_module.mesh_quality_impl(self.CUBE))
        self.assertTrue(result.verdict)
        self.assertIn("收敛", result.verdict)

    def test_invalid_filename_is_rejected(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(mesh_quality_module.mesh_quality_impl("../../etc/passwd.step"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_file_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(mesh_quality_module.mesh_quality_impl("nope.step"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_mesh_is_409(self):
        import os

        import config
        from fastapi import HTTPException

        target = config.UPLOAD_DIR / (self.CUBE + ".msh")
        backup = target.with_suffix(".msh.bak")
        os.replace(target, backup)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(mesh_quality_module.mesh_quality_impl(self.CUBE))
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            os.replace(backup, target)


def write_msh22(path: str, points: np.ndarray, quads_of_tags) -> None:
    """
    手写一个 MSH 2.2 ASCII 文件（只需 $Nodes / $Elements 两段）。

    刻意不用 `gmsh.model.mesh.addNodes/addElements` 来搭夹具：那套 API 的参数
    是"扁平大数组 + 偏移量"的形式（`nodeTags` 必须是扁平列表，嵌套列表会报
    `object of type 'int' has no len()`），把测试写成依赖某个 C++ 绑定的调用
    约定，会让测试因为与业务无关的原因失败。文本格式是人可读、可核对、版本稳
    定的，而且它仍然走完整的 `load_tet_mesh_from_msh` 读取路径。

    ``quads_of_tags`` 是若干组 4 个节点 tag（1 起），组的**顺序就是文件里的顺序**
    ——因此可以故意写反以检验朝向修正。
    """
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes",
             str(points.shape[1])]
    for column in range(points.shape[1]):
        # repr(float) 给出可往返（round-trip）的最短表示，不做任何精度损失
        x, y, z = (repr(float(value)) for value in points[:, column])
        lines.append(f"{column + 1} {x} {y} {z}")
    lines += ["$EndNodes", "$Elements", str(len(quads_of_tags))]
    for number, tags in enumerate(quads_of_tags, start=1):
        joined = " ".join(str(tag) for tag in tags)
        # elm-number elm-type(4=四面体) number-of-tags tags... node-numbers...
        lines.append(f"{number} 4 2 0 {number} {joined}")
    lines += ["$EndElements", ""]
    # newline="\n" 显式指定：`write_text` 默认会把 \n 翻译成 os.linesep，
    # 在 Windows 上写出 CRLF。MSH 解析本身不在乎，但仓库统一用 LF
    # （见 .gitattributes），夹具也照做，免得以后有人 diff 出满屏的 ^M。
    Path(path).write_text("\n".join(lines), encoding="utf-8", newline="\n")


class MeshQualitySampleFileTest(unittest.TestCase):
    """用自己写的网格文件验证（不依赖 gmsh 生成的网格）。"""

    def test_reads_a_mesh_and_reports_analytic_quality(self):
        """
        从文件读回来的网格，其质量必须与直接用数组算的一致。

        第二个单元在文件里**故意写反**节点顺序（5 7 6 8 而不是 5 6 7 8），
        因此这条测试真正钉住的是 `load_tet_mesh_from_msh` 的朝向修正：
        如果那段修正被删掉或写错，这里就会出现 1 个负体积单元，
        而 `non_positive_volume_count` 会从 0 变成 1。

        只断言"读回来有 2 个单元"是抓不到这个的——所以这里断言的是
        解析出来的质量值，并额外断言文件里的反序确实是反的。
        """
        from fe_utils import load_tet_mesh_from_msh

        points = np.concatenate([REGULAR_TET, RIGHT_TET + 5.0], axis=1)
        # 第二组故意反序
        tags_in_file = [[1, 2, 3, 4], [5, 7, 6, 8]]

        # 先确认夹具本身是有效的：文件里那个反序单元的有符号体积必须为负，
        # 否则"朝向被修正了"这条断言就是空转。
        raw_cells = cells_of([0, 1, 2, 3], [4, 6, 5, 7])
        self.assertLess(float(tet_volumes(points, raw_cells)[1]), 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sample.msh")
            write_msh22(path, points, tags_in_file)

            mesh, _faces = load_tet_mesh_from_msh(path)
            self.assertEqual(mesh.t.shape[1], 2)
            summary = summarise(mesh.p, mesh.t)
            # 读回来之后，两个单元都应当是正体积（朝向被修正过）
            self.assertEqual(
                summary["non_positive_volume_count"], 0,
                "朝向修正没有生效：文件里的反序单元读回来仍是负体积",
            )
            self.assertEqual(summary["elements"], 2)
            self.assertAlmostEqual(summary["quality"]["max"], 1.0, places=10)
            self.assertAlmostEqual(
                summary["quality"]["min"], 0.769800358919501, places=10
            )


if __name__ == "__main__":
    unittest.main()
