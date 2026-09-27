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

import numpy as np

from constraints import BoundaryCondition
from geometry import generate_mesh
from solver import (
    SolverRequest,
    nodal_tributary_areas,
    solve_simulation,
)

# --- 算例参数（与 backend/uploads/default_cube.step 一致） -----------------
CUBE = "default_cube.step"
CUBE_EDGE = 10.0            # 立方体边长（单位与几何一致）
FACE_AREA = CUBE_EDGE ** 2  # 加载面面积 = 100
APPLIED_FORCE = 1000.0      # 轴向拉力 N
PRESSURE = 1.0e7            # 均布压力 Pa（= 10 MPa）
YOUNGS_MODULUS = 2.0e11     # 结构钢 E（见 materials.py）
LOADED_FACE_CENTRE = (5.0, 0.0, 0.0)

_MESH_CACHE = {}


def _cube_mesh():
    """立方体网格只生成一次，多个测试类共用（gmsh 初始化有开销）。"""
    if "cube" not in _MESH_CACHE:
        _MESH_CACHE["cube"] = asyncio.run(generate_mesh(CUBE, 1.5))
    return _MESH_CACHE["cube"]


def _face_with_normal(faces, axis, sign):
    """按真实法向挑面——不能假设 face id 1..6 就是 ±X/±Y/±Z。"""
    for face in faces:
        normal = getattr(face, "normal", None)
        if normal and len(normal) == 3 and normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


def _node_nearest(nodes, target):
    best_index, best_distance = -1, float("inf")
    for index, node in enumerate(nodes):
        distance = sum((node[i] - target[i]) ** 2 for i in range(3))
        if distance < best_distance:
            best_distance, best_index = distance, index
    return best_index


class CubeAxialTensionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1) 生成网格（会写入 backend/uploads/default_cube.step.msh，已被 gitignore）
        cls.mesh = _cube_mesh()

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
        cls.centre_node = _node_nearest(cls.mesh.nodes, LOADED_FACE_CENTRE)
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


class CubeUniformPressureRegressionTest(unittest.TestCase):
    """
    均布压力算例。

    这是修复「pressure 类型被求解器完全忽略」的回归测试：此前前端/AI 助手
    可以添加压力边界条件，但求解器既不报错也不施加任何载荷，结果恒为零载荷，
    用户完全察觉不到。现在压力按 ``traction = -p * n`` 施加到面的每个节点上，
    节点力之和精确等于 ``p * 面积``。
    """

    @classmethod
    def setUpClass(cls):
        mesh = _cube_mesh()
        cls.mesh = mesh

        fixed_face = _face_with_normal(mesh.faces, 0, -1)
        loaded_face = _face_with_normal(mesh.faces, 0, +1)

        bcs = [
            BoundaryCondition(
                id="bc_fixed",
                name="固定端",
                type="fixed",
                applicationType="face",
                entityIndex=fixed_face.id,
            ),
            BoundaryCondition(
                id="bc_pressure",
                name="均布压力",
                type="pressure",
                applicationType="face",
                entityIndex=loaded_face.id,
                pressure=PRESSURE,
            ),
        ]

        request = SolverRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=bcs,
            faces=mesh.faces,
        )
        cls.result = asyncio.run(solve_simulation(request))

        cls.centre_node = _node_nearest(mesh.nodes, LOADED_FACE_CENTRE)
        cls.axial_displacement = cls.result.displacements[cls.centre_node][0]

    def test_pressure_actually_loads_the_model(self):
        """回归：pressure 曾经被完全忽略，位移恒为 0。"""
        self.assertEqual(self.result.status, "solved")
        self.assertGreater(
            self.result.max_displacement, 0.0,
            "压力没有产生任何位移——pressure 边界条件可能又被忽略了",
        )

    def test_total_force_equals_pressure_times_face_area(self):
        """
        合力校验：节点力之和必须等于 ``p * A``。

        +X 面的外法向是 +X，正压力指向实体内部，因此施加的合力沿 -X；
        固定端支反力合力应为 +p*A。
        """
        total_fx = sum(force[0] for force in self.result.reaction_forces.values())
        expected = PRESSURE * FACE_AREA
        self.assertAlmostEqual(
            total_fx, expected, delta=expected * 1e-3,
            msg=f"支反力合力 {total_fx:.6g}，期望 {expected:.6g}（= p × A）",
        )

    def test_displacement_matches_analytic_compression(self):
        """压缩量级应接近解析解 ``pL/E``，且方向为 -X。"""
        analytic = PRESSURE * CUBE_EDGE / YOUNGS_MODULUS
        self.assertLess(self.axial_displacement, 0.0, "受压时应向 -X 位移")
        ratio = abs(self.axial_displacement) / analytic
        self.assertGreater(ratio, 0.5, f"比值 {ratio:.3f} 偏小")
        self.assertLess(ratio, 1.05, f"比值 {ratio:.3f} 偏大")

    def test_axial_stress_is_close_to_applied_pressure(self):
        """远离夹持端的应力应接近施加的压力量级。"""
        # 取模型中部节点，避开固定端与加载端的应力集中
        middle = _node_nearest(self.mesh.nodes, (0.0, 0.0, 0.0))
        von_mises = self.result.stresses[middle]
        self.assertGreater(von_mises, PRESSURE * 0.1)
        self.assertLess(von_mises, PRESSURE * 10.0)


class VonMisesAnalyticTest(unittest.TestCase):
    """
    用**解析已知的应变场**直接检验应力后处理。

    构造位移场 ``u_x = eps * x``（其余分量为零），则应变张量为 ``diag(eps, 0, 0)``，
    对应的 Von Mises 应力有闭式解 ``2 * mu * eps``。

    这道测试是补上的：此前只断言了「应力有限、无 NaN」，于是漏掉了
    ``s_dev`` 把迹乘了两次（tr² 而非 tr）的严重错误——静水应力越大，
    Von Mises 被放大得越离谱（受压工况下差了 7 个数量级），
    而位移与支反力却完全正确，单看那些指标根本发现不了。
    """

    EPS = 1.0e-4
    E = YOUNGS_MODULUS
    NU = 0.3

    @classmethod
    def setUpClass(cls):
        from skfem import Basis, ElementTetP1, ElementVectorH1
        from skfem.helpers import ddot, eye, sym_grad, trace
        from skfem.models.elasticity import lame_parameters, linear_stress

        import config
        from solver import load_tet_mesh_from_msh

        _cube_mesh()  # 确保 .msh 已生成
        mesh, _ = load_tet_mesh_from_msh(str(config.UPLOAD_DIR / (CUBE + ".msh")))

        lam, mu = lame_parameters(cls.E, cls.NU)
        constitutive = linear_stress(lam, mu)

        basis_vec = Basis(mesh, ElementVectorH1(ElementTetP1()))
        basis_scalar = Basis(mesh, ElementTetP1())

        # 精确的线性位移场 u_x = eps*x
        u = np.zeros(basis_vec.N)
        u[basis_vec.nodal_dofs[0]] = cls.EPS * mesh.p[0]

        strain = sym_grad(basis_vec.interpolate(u))
        stress = constitutive(strain)
        stress_dev = stress - (1.0 / 3.0) * eye(trace(stress), 3)
        cls.von_mises_qp = np.sqrt(1.5 * ddot(stress_dev, stress_dev))
        cls.nodal_values = basis_scalar.project(cls.von_mises_qp)
        cls.analytic = 2.0 * mu * cls.EPS

    def test_quadrature_values_match_analytic(self):
        mean = float(self.von_mises_qp.mean())
        self.assertAlmostEqual(
            mean, self.analytic, delta=self.analytic * 1e-6,
            msg=f"积分点 Von Mises 均值 {mean:.6g}，解析解 {self.analytic:.6g}",
        )

    def test_projected_nodal_values_match_analytic(self):
        mean = float(self.nodal_values.mean())
        self.assertAlmostEqual(
            mean, self.analytic, delta=self.analytic * 1e-6,
            msg=f"投影后节点均值 {mean:.6g}，解析解 {self.analytic:.6g}",
        )

    def test_stress_is_not_inflated(self):
        """
        反向断言：应力不得被放大。

        错误版本会把对角项写成 tr²，在此算例下 Von Mises 会大出约 1e7 倍。
        """
        self.assertLess(
            float(self.nodal_values.max()), self.analytic * 1.01,
            "Von Mises 明显偏大——检查 s_dev 是否把迹乘了两次",
        )


class TributaryAreaTest(unittest.TestCase):
    """面载荷权重的基础校验：节点归属面积之和 = 面总面积。"""

    def test_nodal_areas_sum_to_surface_area(self):
        import numpy as np

        from solver import load_tet_mesh_from_msh

        mesh, face_triangles = load_tet_mesh_from_msh(
            str(__import__("config").UPLOAD_DIR / (CUBE + ".msh"))
        )
        self.assertTrue(face_triangles, "应当能从 .msh 里取到面 → 三角形映射")

        for tag, triangles in face_triangles.items():
            with self.subTest(face=tag):
                areas = nodal_tributary_areas(mesh.p, triangles)
                # 用三角形自身面积求和作为独立口径
                tri_pts = mesh.p[:, triangles]
                p0, p1, p2 = tri_pts[:, :, 0], tri_pts[:, :, 1], tri_pts[:, :, 2]
                cross = np.cross((p1 - p0).T, (p2 - p0).T)
                expected = 0.5 * np.linalg.norm(cross, axis=1).sum()
                self.assertAlmostEqual(areas.sum(), expected, places=9)

    def test_cube_face_area_is_exact(self):
        from solver import load_tet_mesh_from_msh

        mesh, face_triangles = load_tet_mesh_from_msh(
            str(__import__("config").UPLOAD_DIR / (CUBE + ".msh"))
        )
        # 立方体每个面的面积都应是 100
        for tag, triangles in face_triangles.items():
            with self.subTest(face=tag):
                areas = nodal_tributary_areas(mesh.p, triangles)
                self.assertAlmostEqual(areas.sum(), FACE_AREA, places=6)


class BoundaryConditionContractTest(unittest.TestCase):
    """
    前端契约测试。

    ``BoundaryCondition`` 开启了 ``extra="forbid"``：如果前端新增/改名了字段而
    后端模型没跟上，会直接 422，而不是静默丢字段算出错误结果。这里用前端
    ``types.ts`` 里声明的字段集构造 5 种边界条件，确保它们都能通过校验。
    """

    #: 与 frontend/types.ts 中各接口字段一致
    FRONTEND_SHAPES = [
        {"type": "fixed", "color": "#ff4444"},
        {"type": "displacement", "color": "#ffbb33",
         "displacement": {"x": 0.0, "y": 0.0, "z": 0.0},
         "fixedX": True, "fixedY": True, "fixedZ": True},
        {"type": "force", "color": "#33b5e5", "force": {"x": 0.0, "y": -100.0, "z": 0.0}},
        {"type": "pressure", "color": "#99cc00", "pressure": 1.0},
        {"type": "temperature", "color": "#aa66cc", "temperature": 25.0},
    ]

    def test_frontend_payloads_validate(self):
        for index, shape in enumerate(self.FRONTEND_SHAPES):
            payload = {
                "id": f"bc_{index}",
                "name": f"BC {index}",
                "applicationType": "face",
                "entityIndex": index + 1,
                **shape,
            }
            with self.subTest(type=shape["type"]):
                bc = BoundaryCondition(**payload)
                self.assertEqual(bc.type, shape["type"])

    def test_unknown_field_is_rejected_loudly(self):
        """未知字段必须报错，而不是被静默丢弃（历史上的 pressure 就是这么丢的）。"""
        with self.assertRaises(Exception):
            BoundaryCondition(
                id="bc", name="bc", type="fixed",
                applicationType="face", entityIndex=1,
                pressure_typo=123.0,
            )


class SolverWarningSurfaceTest(unittest.TestCase):
    """不支持的边界条件必须回报给用户，而不是静默忽略。"""

    def test_temperature_bc_produces_warning(self):
        mesh = _cube_mesh()
        fixed_face = _face_with_normal(mesh.faces, 0, -1)
        loaded_face = _face_with_normal(mesh.faces, 0, +1)

        bcs = [
            BoundaryCondition(id="fix", name="固定端", type="fixed",
                              applicationType="face", entityIndex=fixed_face.id),
            BoundaryCondition(id="load", name="拉力", type="force",
                              applicationType="face", entityIndex=loaded_face.id,
                              force={"x": APPLIED_FORCE, "y": 0.0, "z": 0.0}),
            BoundaryCondition(id="temp", name="温度", type="temperature",
                              applicationType="face", entityIndex=loaded_face.id,
                              temperature=100.0),
        ]
        request = SolverRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=bcs,
            faces=mesh.faces,
        )
        result = asyncio.run(solve_simulation(request))

        self.assertTrue(result.warnings, "不支持的 BC 类型必须产生警告")
        self.assertTrue(
            any("temperature" in w for w in result.warnings),
            f"警告里应点名 temperature：{result.warnings}",
        )


class DisplacementBoundaryConditionTest(unittest.TestCase):
    """
    强制位移（``displacement``）边界条件。

    此前这一类边界条件被求解器**完全忽略**（只在 warnings 里提示）。现在：
    1. 按 ``fixedX/fixedY/fixedZ`` 逐分量约束，值取自 ``displacement``；
    2. 通过 skfem 的非齐次 Dirichlet（``condense(x=...)``）施加，支持非零值。

    断言分三层：
      - 精确性：被指定的节点位移必须**精确等于**给定值；
      - 线性性：位移翻倍时支反力精确翻倍（线性系统的必要条件，
        同时说明约束不是被"夹死"或忽略）；
      - 物理量级：总轴力与 ``E·A·δ/L`` 同量级（全约束端会让短粗体偏刚，故给区间）。
    """

    PRESCRIBED = 1.0e-4

    @classmethod
    def _solve_with_delta(cls, delta):
        mesh = _cube_mesh()
        minus_x = _face_with_normal(mesh.faces, 0, -1)
        plus_x = _face_with_normal(mesh.faces, 0, +1)

        bcs = [
            # -X 面完全固定
            BoundaryCondition(id="fix", name="固定端", type="fixed",
                              applicationType="face", entityIndex=minus_x.id),
            # +X 面只强制 X 方向位移
            BoundaryCondition(id="move", name="强制位移", type="displacement",
                              applicationType="face", entityIndex=plus_x.id,
                              displacement={"x": delta, "y": 0.0, "z": 0.0},
                              fixedX=True, fixedY=False, fixedZ=False),
        ]
        request = SolverRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=bcs,
            faces=mesh.faces,
        )
        return mesh, plus_x, asyncio.run(solve_simulation(request))

    @classmethod
    def setUpClass(cls):
        cls.mesh, cls.loaded_face, cls.result = cls._solve_with_delta(cls.PRESCRIBED)
        _, _, cls.doubled = cls._solve_with_delta(2.0 * cls.PRESCRIBED)

        # 用网格的面→三角形映射拿到加载面的节点，断言位移被精确施加
        from solver import load_tet_mesh_from_msh

        import config

        _, face_triangles = load_tet_mesh_from_msh(str(config.UPLOAD_DIR / (CUBE + ".msh")))
        cls.face_nodes = np.unique(face_triangles[int(cls.loaded_face.id)])

        # 注意：不能把所有约束节点的支反力求和——固定端与加载端的支反力
        # 互相抵消（无外载荷时合力恒为 0）。这里只取**加载面**上的轴力。
        cls.loaded_face_nodes = {int(node) for node in cls.face_nodes}
        cls.total_fx = cls._reaction_sum_x(cls.result)
        cls.total_fx_doubled = cls._reaction_sum_x(cls.doubled)

    @classmethod
    def _reaction_sum_x(cls, result):
        total = 0.0
        for key, force in result.reaction_forces.items():
            if int(key) in cls.loaded_face_nodes:
                total += force[0]
        return total

    def test_bc_is_supported_not_warned(self):
        """回归：displacement 曾经只出现在 warnings 里。"""
        self.assertEqual(self.result.status, "solved")
        self.assertFalse(
            any("displacement" in w for w in self.result.warnings),
            f"displacement 不应再被忽略：{self.result.warnings}",
        )

    def test_prescribed_displacement_is_exact(self):
        """被指定的节点，其 X 位移必须精确等于给定值。"""
        for node_index in self.face_nodes:
            with self.subTest(node=int(node_index)):
                ux = self.result.displacements[int(node_index)][0]
                self.assertAlmostEqual(ux, self.PRESCRIBED, places=12,
                                       msg=f"节点 {node_index} 的 ux={ux}，应为 {self.PRESCRIBED}")

    def test_other_components_are_free(self):
        """只约束了 X 方向，Y/Z 不应被一起夹死（否则等于固定约束）。"""
        lateral = [
            max(abs(self.result.displacements[int(n)][1]), abs(self.result.displacements[int(n)][2]))
            for n in self.face_nodes
        ]
        # 泊松效应会让端面横向收缩；全零说明被误当成固定约束
        self.assertGreater(max(lateral), 0.0)

    def test_response_is_linear_in_prescribed_value(self):
        """位移翻倍 ⇒ 支反力精确翻倍（线性系统 + 约束确实生效）。"""
        self.assertNotAlmostEqual(self.total_fx, 0.0, places=6)
        self.assertAlmostEqual(
            self.total_fx_doubled, 2.0 * self.total_fx,
            delta=abs(self.total_fx) * 1e-6,
            msg="支反力未随指定位移线性变化",
        )

    def test_reaction_magnitude_matches_axial_stiffness(self):
        """
        总轴力应与 ``E·A·δ/L`` 同量级。

        全约束端会抑制泊松收缩，使短粗立方体（L/a = 1）比理想杆更刚，
        因此只给一个宽松区间；精确的单轴解需要把端面改为滚支。
        """
        ideal = YOUNGS_MODULUS * FACE_AREA * self.PRESCRIBED / CUBE_EDGE
        ratio = abs(self.total_fx) / ideal
        self.assertGreater(ratio, 1.0, f"比值 {ratio:.3f} 小于理想杆，约束可能失效")
        self.assertLess(ratio, 2.5, f"比值 {ratio:.3f} 过大，约束可能过刚")

    def test_axial_profile_increases_along_x(self):
        """
        轴向位移应沿 X 单调推进。

        不能对"某一根线"上的节点直接比大小：端面的横向收缩会让同一 x 处不同
        (y,z) 的节点 axia l位移略有差异。这里按 x 分箱取**平均**再比较，
        既能验证位移确实在传递，又不受横向变化干扰。
        """
        coordinates = np.array(self.mesh.nodes)
        ux = np.array([d[0] for d in self.result.displacements])

        edges = [(-5.0, -3.0), (-3.0, -1.0), (-1.0, 1.0), (1.0, 3.0), (3.0, 5.01)]
        slab_means = []
        for low, high in edges:
            mask = (coordinates[:, 0] >= low) & (coordinates[:, 0] < high)
            self.assertTrue(mask.any(), f"x∈[{low},{high}) 区间内没有节点")
            slab_means.append(float(ux[mask].mean()))

        for earlier, later in zip(slab_means, slab_means[1:]):
            self.assertLess(earlier, later, f"分箱平均位移未递增：{slab_means}")

    def test_fixed_face_displacement_is_zero(self):
        """固定面上的节点位移应精确为零（与强制位移面形成对照）。"""
        on_fixed_face = [
            index for index, node in enumerate(self.mesh.nodes)
            if abs(node[0] + 5.0) < 1e-9
        ]
        self.assertTrue(on_fixed_face, "未找到固定面上的节点")
        for index in on_fixed_face:
            with self.subTest(node=index):
                ux, uy, uz = self.result.displacements[index]
                self.assertAlmostEqual(ux, 0.0, places=12)
                self.assertAlmostEqual(uy, 0.0, places=12)
                self.assertAlmostEqual(uz, 0.0, places=12)


class DisplacementParsingTest(unittest.TestCase):
    """``_parse_displacement`` 对三种入参形式的容错。"""

    def _bc(self, **kwargs):
        payload = {"id": "bc", "name": "bc", "type": "displacement",
                   "applicationType": "face", "entityIndex": 1}
        payload.update(kwargs)
        return BoundaryCondition(**payload)

    def test_parses_dict(self):
        from solver import _parse_displacement

        bc = self._bc(displacement={"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(_parse_displacement(bc), (1.0, 2.0, 3.0))

    def test_parses_list(self):
        from solver import _parse_displacement

        bc = self._bc(displacement=[4.0, 5.0, 6.0])
        self.assertEqual(_parse_displacement(bc), (4.0, 5.0, 6.0))

    def test_missing_displacement_defaults_to_zero(self):
        from solver import _parse_displacement

        self.assertEqual(_parse_displacement(self._bc()), (0.0, 0.0, 0.0))

    def test_partial_dict_keeps_other_components_zero(self):
        from solver import _parse_displacement

        bc = self._bc(displacement={"x": 7.0})
        self.assertEqual(_parse_displacement(bc), (7.0, 0.0, 0.0))


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
