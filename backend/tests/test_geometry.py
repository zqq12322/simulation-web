"""
几何 / 网格提取的回归测试。

这些都对应曾经真实出现过的缺陷：
- 误用 Gmsh API 导致**所有面法向为 None、面积恒为 0**；
- 边界条件因此退化成「包围盒 6 个面猜」。
所以这里用解析值把正确行为钉死。
"""

import asyncio
import math
import unittest

from geometry import generate_mesh, get_geometry_metadata

# backend/uploads/test_part.step：10x10x10 方块，中心挖 Phi6 通孔
PART = "test_part.step"
BLOCK_EDGE = 10.0
HOLE_RADIUS = 3.0
FULL_FACE_AREA = BLOCK_EDGE ** 2                  # 100
HOLED_FACE_AREA = FULL_FACE_AREA - math.pi * HOLE_RADIUS ** 2
CYLINDER_AREA = 2 * math.pi * HOLE_RADIUS * BLOCK_EDGE  # 2*pi*r*h = 188.5


class GeometryMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = asyncio.run(get_geometry_metadata(PART))

    def test_identifies_expected_topology(self):
        self.assertEqual(len(self.metadata.faces), 7)
        self.assertEqual(len(self.metadata.edges), 15)
        self.assertEqual(len(self.metadata.vertices), 10)

    def test_every_face_has_normal_and_area(self):
        """回归：这两个字段曾经全是 None / 0.0。"""
        for face in self.metadata.faces:
            with self.subTest(face=face.id):
                self.assertIsNotNone(face.normal, f"面 {face.id} 缺少法向")
                self.assertEqual(len(face.normal), 3)
                magnitude = math.sqrt(sum(c * c for c in face.normal))
                self.assertAlmostEqual(magnitude, 1.0, places=6, msg="法向应为单位向量")
                self.assertGreater(face.area, 0.0, f"面 {face.id} 面积为 0")

    def test_planar_face_areas_match_analytic(self):
        planes = [f for f in self.metadata.faces if f.type == "Plane"]
        self.assertEqual(len(planes), 6)
        areas = sorted(f.area for f in planes)
        # 4 个完整侧面（100）与 2 个挖孔面（71.73）
        self.assertEqual(sum(1 for a in areas if abs(a - FULL_FACE_AREA) < 1e-6), 4)
        self.assertEqual(
            sum(1 for a in areas if abs(a - HOLED_FACE_AREA) < 1e-3), 2
        )

    def test_cylinder_area_matches_analytic(self):
        cylinders = [f for f in self.metadata.faces if f.type == "Cylinder"]
        self.assertEqual(len(cylinders), 1)
        self.assertAlmostEqual(
            cylinders[0].area, CYLINDER_AREA, delta=CYLINDER_AREA * 0.01
        )

    def test_axis_aligned_normals(self):
        """方块的面法向必须是干净的轴向单位向量。"""
        for face in self.metadata.faces:
            if face.type != "Plane":
                continue
            with self.subTest(face=face.id):
                aligned = [round(c, 6) for c in face.normal]
                self.assertEqual(sum(1 for c in aligned if abs(c) == 1.0), 1)
                self.assertEqual(sum(1 for c in aligned if c == 0.0), 2)

    def test_edges_have_positive_length(self):
        for edge in self.metadata.edges:
            with self.subTest(edge=edge.id):
                self.assertGreater(edge.length, 0.0)


class GenerateMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = asyncio.run(generate_mesh(PART, 1.5))

    def test_produces_volume_mesh(self):
        self.assertGreater(len(self.mesh.nodes), 100)
        self.assertGreater(len(self.mesh.elements), 100)

    def test_elements_are_tetrahedra_with_valid_indices(self):
        node_count = len(self.mesh.nodes)
        for element in self.mesh.elements[:200]:
            self.assertEqual(len(element), 4)
            for index in element:
                self.assertGreaterEqual(index, 0)
                self.assertLess(index, node_count)

    def test_mesh_has_faces_and_metadata(self):
        self.assertEqual(self.mesh.status, "success")
        self.assertEqual(len(self.mesh.faces), 7)
        self.assertTrue(all(f.normal is not None for f in self.mesh.faces))

    def test_rejects_invalid_mesh_size(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(generate_mesh(PART, 1e9))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_path_traversal(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(generate_mesh("../../secret.step", 1.0))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_file_returns_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(generate_mesh("does_not_exist.step", 1.0))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
