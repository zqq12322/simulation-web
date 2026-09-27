"""
求解器物理回归测试（本套件里最重要的一个）。

思路：拿一个有**解析解**的算例来锁定求解器的正确性——
10x10x10 结构钢立方体，一端面全约束、对端面施加 1000 N 轴向拉力。

断言三件事：
1. 全局平衡：支反力合力与施加载荷精确抵消；
2. 位移量级：加载面中心轴向位移与 ``FL/AE`` 同量级，且因全约束端略刚而
   落在 (0.5, 1.05) 区间内；
3. 结果有限：应力无 NaN。

这样任何一次改动如果破坏了刚度矩阵装配、边界条件施加或应力后处理，
都会在 CI 上立刻暴露，而不是等到用户发现结果不对。

注意：本测试**不需要启动服务器**（直接调用端点函数），因此适合放进 CI。
"""

import asyncio
import unittest

from constraints import BoundaryCondition
from geometry import generate_mesh
from solver import SolverRequest, solve_simulation

# --- 算例参数（与 backend/uploads/default_cube.step 一致） -----------------
CUBE = "default_cube.step"
CUBE_EDGE = 10.0            # 立方体边长（单位与几何一致）
FACE_AREA = CUBE_EDGE ** 2  # 加载面面积 = 100
APPLIED_FORCE = 1000.0      # 轴向拉力 N
YOUNGS_MODULUS = 2.0e11     # 结构钢 E（见 materials.py）
LOADED_FACE_CENTRE = (5.0, 0.0, 0.0)


def _face_with_normal(faces, axis, sign):
    """按真实法向挑面——不能假设 face id 1..6 就是 ±X/±Y/±Z。"""
    for face in faces:
        normal = getattr(face, "normal", None)
        if normal and len(normal) == 3 and normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


class CubeAxialTensionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1) 生成网格（会写入 backend/uploads/default_cube.step.msh，已被 gitignore）
        cls.mesh = asyncio.run(generate_mesh(CUBE, 1.5))

        fixed_face = _face_with_normal(cls.mesh.faces, 0, -1)
        loaded_face = _face_with_normal(cls.mesh.faces, 0, +1)

        bcs = [
            BoundaryCondition(
                id="bc_fixed",
                name="固定端",
                type="fixed",
                applicationType="face",
                entityIndex=fixed_face.id,
            ),
            BoundaryCondition(
                id="bc_load",
                name="轴向拉力",
                type="force",
                applicationType="face",
                entityIndex=loaded_face.id,
                force={"x": APPLIED_FORCE, "y": 0.0, "z": 0.0},
            ),
        ]

        request = SolverRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=bcs,
            faces=cls.mesh.faces,
        )

        # 2) 求解
        cls.result = asyncio.run(solve_simulation(request))

        # 3) 找最接近加载面中心的节点，用于取轴向位移
        best_index, best_distance = -1, float("inf")
        for index, node in enumerate(cls.mesh.nodes):
            distance = sum((node[i] - LOADED_FACE_CENTRE[i]) ** 2 for i in range(3))
            if distance < best_distance:
                best_distance, best_index = distance, index
        cls.centre_node = best_index
        cls.axial_displacement = cls.result.displacements[cls.centre_node][0]

    def test_solver_reports_success(self):
        self.assertEqual(self.result.status, "solved")
        self.assertGreater(len(self.result.displacements), 0)
        self.assertEqual(len(self.result.stresses), len(self.result.displacements))

    def test_global_equilibrium(self):
        """支反力合力必须与施加载荷精确抵消（数值容差 1 N）。"""
        total_fx = sum(force[0] for force in self.result.reaction_forces.values())
        self.assertAlmostEqual(
            total_fx,
            -APPLIED_FORCE,
            delta=1.0,
            msg=f"X 向支反力合力 {total_fx}，期望 {-APPLIED_FORCE}",
        )

    def test_axial_displacement_within_analytic_range(self):
        """
        与解析解 FL/AE 对比。

        全约束端会抑制泊松收缩，使结构比自由杆略刚，因此比值应略小于 1；
        粗网格线性四面体又会带来离散误差，故给一个宽松但有效的区间。
        """
        analytic = APPLIED_FORCE * CUBE_EDGE / (FACE_AREA * YOUNGS_MODULUS)
        ratio = self.axial_displacement / analytic
        self.assertGreater(ratio, 0.5, f"比值 {ratio:.3f} 偏小，结构可能过刚")
        self.assertLess(ratio, 1.05, f"比值 {ratio:.3f} 偏大，结构可能过软")
        self.assertGreater(self.axial_displacement, 0.0, "轴向位移方向应为正（受拉）")

    def test_transverse_displacement_is_smaller_than_axial(self):
        """泊松效应导致的横向位移应明显小于轴向位移。"""
        dx, dy, dz = self.result.displacements[self.centre_node]
        transverse = max(abs(dy), abs(dz))
        self.assertLess(transverse, abs(dx))

    def test_stresses_are_finite(self):
        for value in self.result.stresses:
            self.assertFalse(value != value, "应力中出现 NaN")  # NaN != NaN
            self.assertNotEqual(value, float("inf"))
        self.assertGreaterEqual(self.result.max_stress, 0.0)
        self.assertGreater(self.result.max_displacement, 0.0)


class SolverInputValidationTest(unittest.TestCase):
    def test_rejects_path_traversal_filename(self):
        from fastapi import HTTPException

        request = SolverRequest(
            geometry_filename="../../etc/passwd.step",
            material_id="structural_steel",
            boundary_conditions=[],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_simulation(request))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_unknown_material(self):
        from fastapi import HTTPException

        request = SolverRequest(
            geometry_filename=CUBE,
            material_id="unobtainium",
            boundary_conditions=[],
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_simulation(request))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
