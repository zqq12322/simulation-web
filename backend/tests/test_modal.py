"""
模态分析的解析校验。

本文件的断言全部来自**闭式解或精确不变量**，没有一条是"结果有限/非 NaN"：

1. `RigidBodyInvariantTest`——对任意四面体网格，解析给出的 6 个刚体模态
   （3 平移 + 3 绕形心转动）必须满足 ``K u = 0``（机器精度），
   且其动能 ``uᵀMu`` 必须等于解析惯量（平移 ``ρV``、转动 ``ρV(w²+h²)/12``）。
   这一条同时锁死了**刚度矩阵**与**一致质量矩阵**的装配，
   而且与特征值求解器无关（换求解器也照样成立）。

2. `FreeFreeCubeTest`——自由-自由结构必须恰好有 6 个零特征值，
   紧跟着才是弹性模态。这是"质量/刚度整体装配正确"的强判据：
   少一个零模态说明约束被误加，多一个说明矩阵错了。

3. `ConfinedRodAxialFrequencyTest`——全域滚动支承的细杆，轴向频率有解析解
   ``f_n = (2n-1)/(4L)·√((λ+2μ)/ρ)``。**关键是有效模量是侧限模量
   ``E(1-ν)/((1+ν)(1-2ν))`` 而不是 E**（ν=0.3 时两者差 16%），
   所以这条测试同时验证了三维本构关系与参数取值。实测二阶收敛。

4. `ModalUnitScalingTest`——同一份网格按 m 与 mm 解释，频率必须相差**恰好
   1000 倍**（``ω ∝ √(E/ρ)/L``；初版误写成"完全相同"，是个真实的物理错误）。

5. `ModalApiContractTest`——端到端（走 `solve_modal_impl`）：默认立方体固支后
   频率为正且升序、振型已归一化、载荷类边界条件给出警告、错误码正确。
"""

import asyncio
import unittest

import numpy as np
from fastapi import HTTPException
from skfem import Basis, ElementTetP1, ElementVectorH1, MeshTet, asm
from skfem.models.elasticity import lame_parameters, linear_elasticity

import config
from constraints import BoundaryCondition
from geometry import generate_mesh
from modal import (
    MAX_MODES,
    ModalRequest,
    solve_generalized_modes,
    solve_modal_impl,
    vector_mass,
)

CUBE = "default_cube.step"
CUBE_EDGE = 10.0
STEEL_E = 2.0e11
STEEL_NU = 0.3
STEEL_RHO = 7850.0

_MESH_CACHE = {}


def _cube_mesh():
    if "cube" not in _MESH_CACHE:
        _MESH_CACHE["cube"] = asyncio.run(generate_mesh(CUBE, 1.5))
    return _MESH_CACHE["cube"]


def _face_with_normal(faces, axis, sign):
    for face in faces:
        normal = getattr(face, "normal", None)
        if normal and len(normal) == 3 and normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


def structured_box(length, width, height, nx, ny, nz):
    """
    长方体 → 结构化四面体网格。

    每个六面体按共享主对角线的方式切成 6 个四面体。**必须自检**：
    这是手写索引最容易出错的地方（把四个共面的点组成"四面体"会得到零体积单元，
    scikit-fem 只会在很后面抛一个 divide-by-zero 警告）。
    所以这里直接断言"所有单元体积为正"且"体积之和精确等于长方体体积"。
    """
    xs = np.linspace(0.0, length, nx + 1)
    ys = np.linspace(0.0, width, ny + 1)
    zs = np.linspace(0.0, height, nz + 1)
    grid = np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), axis=-1)
    points = grid.reshape(-1, 3)

    def nid(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    tets = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                c = {
                    (a, b, d): nid(i + a, j + b, k + d)
                    for a in (0, 1) for b in (0, 1) for d in (0, 1)
                }
                v000, v100, v010 = c[(0, 0, 0)], c[(1, 0, 0)], c[(0, 1, 0)]
                v110, v001, v101 = c[(1, 1, 0)], c[(0, 0, 1)], c[(1, 0, 1)]
                v011, v111 = c[(0, 1, 1)], c[(1, 1, 1)]
                tets += [
                    [v000, v100, v110, v111],
                    [v000, v110, v010, v111],
                    [v000, v010, v011, v111],
                    [v000, v011, v001, v111],
                    [v000, v001, v101, v111],
                    [v000, v101, v100, v111],
                ]
    t = np.array(tets, dtype=np.int64).T

    p = points.T
    v0, v1, v2, v3 = (p[:, t[i]] for i in range(4))
    det = np.einsum("ij,ij->i", np.cross((v1 - v0).T, (v2 - v0).T), (v3 - v0).T)
    assert np.all(det > 0), f"结构化网格里出现了非正体积单元：{det.min()}"
    volume = float(det.sum() / 6.0)
    expected = length * width * height
    assert abs(volume - expected) < 1e-12 * expected, f"体积 {volume} != {expected}"

    return MeshTet(points.T, t)


def assemble(mesh, youngs=STEEL_E, poisson=STEEL_NU, density=STEEL_RHO):
    basis = Basis(mesh, ElementVectorH1(ElementTetP1()))
    lam, mu = lame_parameters(youngs, poisson)
    stiffness = asm(linear_elasticity(lam, mu), basis)
    mass = density * asm(vector_mass, basis)
    return basis, stiffness, mass


def _to_dofs(basis, nodal_field):
    vector = np.zeros(basis.N)
    for axis in range(3):
        vector[basis.nodal_dofs[axis]] = nodal_field[axis]
    return vector


def rigid_body_fields(mesh):
    """解析刚体模态位移场：3 个平移 + 3 个绕形心转动。"""
    coordinates = mesh.p
    relative = coordinates - coordinates.mean(axis=1, keepdims=True)
    fields = []
    for axis in range(3):
        field = np.zeros_like(coordinates)
        field[axis] = 1.0
        fields.append(field)
    for axis in range(3):
        unit = np.zeros(3)
        unit[axis] = 1.0
        omega = np.broadcast_to(unit[:, None], relative.shape).T
        fields.append(np.cross(omega, relative.T).T)
    return fields


class RigidBodyInvariantTest(unittest.TestCase):
    """
    刚体模态的精确不变量——本套件里最强的一条断言。

    对**任意**网格都成立，且与特征值求解器无关：
      * ``K u = 0``（刚体运动不产生应变能）——验证刚度矩阵；
      * ``uᵀMu = 解析惯量``——验证一致质量矩阵。
    """

    LENGTH, WIDTH, HEIGHT = 1.0, 0.02, 0.02

    @classmethod
    def setUpClass(cls):
        cls.mesh = structured_box(cls.LENGTH, cls.WIDTH, cls.HEIGHT, 10, 1, 1)
        cls.basis, cls.stiffness, cls.mass = assemble(cls.mesh)
        cls.fields = rigid_body_fields(cls.mesh)
        cls.dofs = [_to_dofs(cls.basis, field) for field in cls.fields]
        cls.volume = cls.LENGTH * cls.WIDTH * cls.HEIGHT

    def test_rigid_motions_have_zero_strain_energy(self):
        """K u 必须是（相对量级下的）零向量。"""
        scale = float(np.abs(self.stiffness).max())
        for name, dof_vector in zip(
            ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"], self.dofs
        ):
            with self.subTest(mode=name):
                residual = self.stiffness @ dof_vector
                relative = float(np.linalg.norm(residual)) / (
                    scale * float(np.linalg.norm(dof_vector)) + 1e-300
                )
                self.assertLess(
                    relative, 1e-14,
                    f"刚体模态 {name} 产生了非零应变能（相对残差 {relative:.3e}）"
                    "——刚度矩阵装配可能有问题",
                )

    def test_translational_mass_equals_density_times_volume(self):
        """平移模态的动能 ``uᵀMu`` 应等于总质量 ``ρV``。"""
        expected = STEEL_RHO * self.volume
        for name in ("Tx", "Ty", "Tz"):
            with self.subTest(mode=name):
                dof_vector = self.dofs[["Tx", "Ty", "Tz"].index(name)]
                got = float(dof_vector @ (self.mass @ dof_vector))
                self.assertAlmostEqual(
                    got / expected, 1.0, places=12,
                    msg=f"{name} 的质量 {got:.10g}，解析 ρV = {expected:.10g}",
                )

    def test_rotational_inertia_matches_analytic(self):
        """转动模态的 ``uᵀMu`` 应等于解析转动惯量。"""
        expected = {
            # u = e_x × r = (0, -z, y) ⇒ |u|² = y² + z²
            "Rx": STEEL_RHO * self.volume * (self.WIDTH ** 2 + self.HEIGHT ** 2) / 12.0,
            # u = e_y × r = (z, 0, -x) ⇒ |u|² = x² + z²
            "Ry": STEEL_RHO * self.volume * (self.LENGTH ** 2 + self.HEIGHT ** 2) / 12.0,
            # u = e_z × r = (-y, x, 0) ⇒ |u|² = x² + y²
            "Rz": STEEL_RHO * self.volume * (self.LENGTH ** 2 + self.WIDTH ** 2) / 12.0,
        }
        names = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        for name, reference in expected.items():
            with self.subTest(mode=name):
                dof_vector = self.dofs[names.index(name)]
                got = float(dof_vector @ (self.mass @ dof_vector))
                self.assertAlmostEqual(
                    got / reference, 1.0, places=12,
                    msg=f"{name} 的转动惯量 {got:.10g}，解析 {reference:.10g}",
                )


class FreeFreeCubeTest(unittest.TestCase):
    """自由-自由结构：必须恰好 6 个零特征值，随后是弹性模态。"""

    @classmethod
    def setUpClass(cls):
        cls.mesh = structured_box(1.0, 1.0, 1.0, 2, 2, 2)
        cls.basis, cls.stiffness, cls.mass = assemble(cls.mesh)
        # 无任何约束 —— 这正是 K 奇异的场景，也是 shift 选择必须处理的场景
        cls.eigenvalues, cls.modes, cls.info = solve_generalized_modes(
            cls.stiffness, cls.mass, np.array([], dtype=np.int64), num_modes=9
        )

    def test_has_exactly_six_zero_eigenvalues(self):
        scale = float(np.abs(self.eigenvalues).max())
        zero_like = np.abs(self.eigenvalues) < 1e-8 * scale
        self.assertEqual(
            int(np.count_nonzero(zero_like)), 6,
            f"自由-自由结构应有 6 个刚体模态，实得 {int(np.count_nonzero(zero_like))}；"
            f"λ = {self.eigenvalues}",
        )

    def test_first_elastic_mode_is_positive_and_separated(self):
        elastic = self.eigenvalues[6:]
        self.assertTrue(np.all(elastic > 0.0), f"弹性模态应为正：{elastic}")
        self.assertGreater(
            elastic[0], 1e6,
            f"第一个弹性特征值 λ₇ = {elastic[0]:.6g} 过小，可能与刚体模态混淆",
        )

    def test_rigid_modes_are_actually_rigid(self):
        """解出来的 6 个零模态，其应变能必须可以忽略（而不是"数值上很小"）。"""
        stiffness_scale = float(np.abs(self.stiffness).max())
        for index in range(6):
            mode = self.modes[:, index]
            energy = float(mode @ (self.stiffness @ mode))
            kinetic = float(mode @ (self.mass @ mode))
            with self.subTest(mode=index):
                self.assertLess(
                    abs(energy) / (stiffness_scale * kinetic), 1e-10,
                    f"第 {index} 阶疑似零模态的应变能不可忽略——它并不是刚体模态",
                )

    def test_mode_vectors_are_mass_orthonormal(self):
        """广义特征向量必须满足 φᵢᵀMφⱼ = δᵢⱼ（ARPACK 的 M-正交性）。"""
        gram = self.modes.T @ (self.mass @ self.modes)
        self.assertLess(
            float(np.abs(gram - np.eye(gram.shape[0])).max()), 1e-8,
            "振型不再满足质量正交性，特征值问题的求解可能被破坏",
        )

    def test_num_free_dofs_reported(self):
        self.assertEqual(self.info["num_free_dofs"], self.basis.N)
        self.assertEqual(self.info["num_constrained_dofs"], 0)
        self.assertFalse(self.info["modes_truncated"])


class ConfinedRodAxialFrequencyTest(unittest.TestCase):
    """
    全域滚动支承细杆的轴向振动频率 vs 解析解。

    解析解
    ------
    ``u_y = u_z = 0`` 意味着材料**不能横向收缩**，于是有效模量不是 E，而是
    侧限模量（λ + 2μ）::

        E_conf = E(1 - ν) / ((1 + ν)(1 - 2ν))

    ν = 0.3 时为 1.3462·E，即频率高出 16.0%——正好是"误用 E 做参考"会看到的偏差
    （本套件的初版就是这么错的，靠数值实验才发现）。固定-自由杆的轴向频率为::

        f_n = (2n - 1) / (4L) · √(E_conf / ρ)

    这条断言因此比单纯的"频率对得上"更强：它同时验证了三维本构关系。
    """

    LENGTH = 1.0
    WIDTH = HEIGHT = 0.02

    def _rod_frequencies(self, nx, poisson=STEEL_NU):
        mesh = structured_box(self.LENGTH, self.WIDTH, self.HEIGHT, nx, 1, 1)
        basis, stiffness, mass = assemble(mesh, poisson=poisson)

        constrained = []
        # 全域滚动支承：横向两个分量全部约束
        for axis in (1, 2):
            constrained.extend(basis.nodal_dofs[axis].tolist())
        # 固定端（x = 0）只约束轴向
        on_root = mesh.p[0] < 1e-12
        constrained.extend(basis.nodal_dofs[0][on_root].tolist())

        eigenvalues, _, _ = solve_generalized_modes(
            stiffness, mass, np.asarray(constrained, dtype=np.int64), num_modes=3
        )
        return np.sqrt(eigenvalues) / (2.0 * np.pi)

    def _analytic(self, order, poisson=STEEL_NU):
        confined = STEEL_E * (1.0 - poisson) / ((1.0 + poisson) * (1.0 - 2.0 * poisson))
        return np.array(
            [(2 * n - 1) / (4.0 * self.LENGTH) * np.sqrt(confined / STEEL_RHO)
             for n in order]
        )

    def test_confined_modulus_differs_from_youngs_modulus(self):
        """先把这个"坑"本身固化成断言：两者差 16%，不是笔误。"""
        confined = STEEL_E * (1 - STEEL_NU) / ((1 + STEEL_NU) * (1 - 2 * STEEL_NU))
        ratio = np.sqrt(confined / STEEL_E)
        self.assertAlmostEqual(ratio, 1.1602, places=4)
        self.assertNotAlmostEqual(ratio, 1.0, places=2)

    def test_first_three_frequencies_match_analytic(self):
        """
        前三阶轴向频率与解析解对比。

        容差随阶次放宽是**物理上应该的**，不是为了让测试通过：一致质量矩阵下
        P1 单元第 n 阶频率的相对误差是 ``O(n²h²)``，因此高阶的误差按
        ``(2n-1)²`` 增长。实测 nx=32 时前三阶误差约 1.0e-4 / 8.8e-4 / 2.4e-3，
        比值 1 : 9 : 25 ≈ 1 : 3² : 5²，正好是 ``(2n-1)²``。
        """
        numeric = self._rod_frequencies(nx=32)
        reference = self._analytic([1, 2, 3])

        # 一阶最准，可以卡得很紧
        self.assertAlmostEqual(
            numeric[0] / reference[0], 1.0, places=3,
            msg=f"一阶轴向频率 {numeric[0]:.6g} vs 解析 {reference[0]:.6g}",
        )
        # 全部三阶：容差 5e-3，覆盖 O(n²h²) 的高阶误差
        np.testing.assert_allclose(
            numeric / reference, 1.0, rtol=5e-3,
            err_msg=(
                f"轴向频率与解析解不符：数值 {numeric}，解析 {reference}。"
                "若整体偏高约 16%，说明有效模量算错了（应为侧限模量 λ+2μ）"
            ),
        )

    def test_high_order_error_grows_like_mode_number_squared(self):
        """
        误差按 ``(2n-1)²`` 增长——这本身就说明离散格式是对的。

        如果哪天有人把一致质量矩阵换成集中质量矩阵，误差的分布形态会明显改变，
        这条断言会先于容差断言失败。
        """
        reference = self._analytic([1, 2, 3])
        errors = np.abs(self._rod_frequencies(nx=16) / reference - 1.0)
        expected_ratio = np.array([(2 * n - 1) ** 2 for n in (1, 2, 3)], dtype=float)
        expected_ratio /= expected_ratio[0]
        got_ratio = errors / errors[0]
        np.testing.assert_allclose(
            got_ratio, expected_ratio, rtol=0.25,
            err_msg=f"误差分布 {got_ratio} 与 (2n-1)² 预期 {expected_ratio} 不符",
        )

    def test_frequencies_converge_quadratically(self):
        """
        P1 单元 + 一致质量矩阵的轴向频率误差应为 O(h²)。

        断言的是**收敛性与收敛阶**，比单个网格上的容差更有说服力：
        网格加密一倍，误差应降到约 1/4。
        """
        reference = self._analytic([1])[0]
        errors = []
        for nx in (4, 8, 16, 32):
            numeric = self._rod_frequencies(nx)[0]
            errors.append(abs(numeric / reference - 1.0))

        # 误差单调下降
        for coarser, finer in zip(errors, errors[1:]):
            self.assertLess(finer, coarser, f"网格加密后误差未下降：{errors}")
        # 收敛阶 ≈ 2（加密一倍误差降到 1/4±容差）
        for coarser, finer in zip(errors, errors[1:]):
            self.assertGreater(coarser / finer, 3.0)
            self.assertLess(coarser / finer, 5.0)
        # 最细网格上误差很小
        self.assertLess(errors[-1], 1e-3)

    def test_zero_poisson_reduces_to_youngs_modulus(self):
        """ν = 0 时侧限模量退化为 E——两头都对，才说明公式没凑数。"""
        numeric = self._rod_frequencies(nx=32, poisson=0.0)
        reference = self._analytic([1, 2, 3], poisson=0.0)
        self.assertAlmostEqual(reference[0], np.sqrt(STEEL_E / STEEL_RHO) / 4.0, places=6)
        np.testing.assert_allclose(numeric / reference, 1.0, rtol=5e-3)


class ModalUnitScalingTest(unittest.TestCase):
    """
    长度单位链路：同一份网格按 m / mm 解释，频率必须相差**恰好 1000 倍**。

    这是很容易写错的一条（初版就写成了"频率应当完全相同"）：单位改变的是
    **几何的真实尺寸**——同一串坐标按 m 读是 10 m、按 mm 读是 0.01 m，
    而 ``ω ∝ √(E/ρ) / L``，所以尺寸小 1000 倍，频率就大 1000 倍。
    换句话说：**频率不是无量纲量，它随尺寸缩放**；这也正是"10 单位的零件
    按 m 还是按 mm 解释"会得出完全不同物理结论的原因。

    断言 1000 倍是精确关系（不含离散误差），因此可以用很小的容差。
    """

    SCALE = 1000.0

    @classmethod
    def setUpClass(cls):
        mesh = _cube_mesh()
        fixed_face = _face_with_normal(mesh.faces, 0, -1)
        bcs = [
            BoundaryCondition(id="fix", name="固定端", type="fixed",
                              applicationType="face", entityIndex=fixed_face.id),
        ]
        cls.results = {}
        for unit in ("m", "mm"):
            cls.results[unit] = asyncio.run(solve_modal_impl(ModalRequest(
                geometry_filename=CUBE,
                material_id="structural_steel",
                boundary_conditions=bcs,
                faces=mesh.faces,
                length_unit=unit,
                num_modes=6,
            )))

    def test_frequency_scales_inversely_with_length(self):
        meter = np.asarray(self.results["m"].frequencies)
        millimeter = np.asarray(self.results["mm"].frequencies)
        self.assertGreater(meter[0], 0.0)
        np.testing.assert_allclose(
            millimeter / meter, self.SCALE, rtol=1e-6,
            err_msg="换长度单位后频率没有按 1/L 缩放——单位换算链路有问题",
        )

    def test_millimeter_interpretation_is_physically_meaningful(self):
        """
        按毫米解释时，"10 mm 钢立方体单面固支"的一阶频率应在几十~几百 kHz。

        1 cm 的钢块刚度大、质量小，这是超声量级，量级核对用的。
        """
        frequency = self.results["mm"].frequencies[0]
        self.assertGreater(frequency, 1.0e4, f"f₁ = {frequency:.4g} Hz 偏小")
        self.assertLess(frequency, 1.0e7, f"f₁ = {frequency:.4g} Hz 偏大")

    def test_results_declare_their_length_unit(self):
        self.assertEqual(self.results["m"].length_unit, "m")
        self.assertEqual(self.results["mm"].length_unit, "mm")
        self.assertEqual(self.results["m"].units["frequency"], "Hz")


class ModalApiContractTest(unittest.TestCase):
    """端到端契约：默认立方体 + 固支，检查返回结构与错误码。"""

    @classmethod
    def setUpClass(cls):
        mesh = _cube_mesh()
        cls.mesh = mesh
        cls.fixed_face = _face_with_normal(mesh.faces, 0, -1)
        cls.fixed_bc = BoundaryCondition(
            id="fix", name="固定端", type="fixed",
            applicationType="face", entityIndex=cls.fixed_face.id,
        )
        cls.result = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=[cls.fixed_bc],
            faces=mesh.faces,
            length_unit="mm",
            num_modes=6,
        )))

    def test_reports_success_and_unit(self):
        self.assertEqual(self.result.status, "solved")
        self.assertEqual(self.result.length_unit, "mm")
        self.assertEqual(self.result.units["frequency"], "Hz")

    def test_frequencies_are_positive_and_ascending(self):
        frequencies = self.result.frequencies
        self.assertEqual(len(frequencies), 6)
        self.assertTrue(all(f > 0.0 for f in frequencies), f"出现非正频率：{frequencies}")
        self.assertTrue(
            all(a <= b for a, b in zip(frequencies, frequencies[1:])),
            f"频率未按升序返回：{frequencies}",
        )

    def test_angular_frequency_and_eigenvalue_are_consistent(self):
        """ω = 2πf 且 λ = ω²——三个数组必须自洽，否则前端会显示错。"""
        for frequency, angular, eigenvalue in zip(
            self.result.frequencies,
            self.result.angular_frequencies,
            self.result.eigenvalues,
        ):
            with self.subTest(f=frequency):
                self.assertAlmostEqual(angular / (2 * np.pi) / frequency, 1.0, places=12)
                self.assertAlmostEqual(eigenvalue / angular ** 2, 1.0, places=12)

    def test_no_rigid_body_modes_when_fixed(self):
        """固支后不应再有刚体模态。"""
        self.assertEqual(self.result.rigid_body_modes, 0)

    def test_mode_shapes_are_normalized(self):
        self.assertEqual(len(self.result.mode_shapes), len(self.result.frequencies))
        for index, shape in enumerate(self.result.mode_shapes):
            with self.subTest(mode=index):
                array = np.asarray(shape)
                self.assertEqual(array.shape[1], 3)
                peak = float(np.linalg.norm(array, axis=1).max())
                self.assertAlmostEqual(peak, 1.0, places=10,
                                       msg=f"第 {index} 阶振型未归一化到最大位移 1")

    def test_fixed_nodes_have_zero_displacement_in_every_mode(self):
        """固支面上的节点在每个振型里位移都必须为零（齐次 Dirichlet）。"""
        on_fixed_face = [
            index for index, node in enumerate(self.mesh.nodes)
            if abs(node[0] + 5.0) < 1e-9
        ]
        self.assertTrue(on_fixed_face, "未找到固定面上的节点")
        for index, shape in enumerate(self.result.mode_shapes):
            array = np.asarray(shape)
            with self.subTest(mode=index):
                self.assertLess(float(np.abs(array[on_fixed_face]).max()), 1e-12)

    def test_free_free_gives_six_rigid_modes(self):
        """不加任何约束时报告 6 个刚体模态，并给出可操作的警告。"""
        result = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=[],
            faces=self.mesh.faces,
            length_unit="mm",
            num_modes=8,
        )))
        self.assertEqual(result.rigid_body_modes, 6)
        self.assertTrue(
            any("刚体模态" in warning for warning in result.warnings),
            f"应提示自由模态：{result.warnings}",
        )

    def test_load_based_bc_is_ignored_with_explanation(self):
        """载荷类边界条件必须被忽略**并说明原因**（固有频率与载荷无关）。"""
        bcs = [
            self.fixed_bc,
            BoundaryCondition(id="pull", name="拉力", type="force",
                              applicationType="face",
                              entityIndex=_face_with_normal(self.mesh.faces, 0, 1).id,
                              force={"x": 1000.0, "y": 0.0, "z": 0.0}),
            BoundaryCondition(id="temp", name="温度", type="temperature",
                              applicationType="face",
                              entityIndex=self.fixed_face.id, temperature=100.0),
        ]
        result = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=bcs,
            faces=self.mesh.faces,
            length_unit="mm",
            num_modes=3,
        )))
        self.assertEqual(len(result.warnings), 2, f"警告应各一条：{result.warnings}")
        self.assertTrue(any("force" in w for w in result.warnings))
        self.assertTrue(any("temperature" in w for w in result.warnings))
        self.assertTrue(any("与载荷大小无关" in w for w in result.warnings))
        # 加了 1000 N 也不该改变频率——这就是上面那条警告的物理内容
        baseline = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=[self.fixed_bc],
            faces=self.mesh.faces,
            length_unit="mm",
            num_modes=3,
        )))
        np.testing.assert_allclose(result.frequencies, baseline.frequencies, rtol=1e-12)

    def test_displacement_bc_constrains_only_selected_components(self):
        """
        ``displacement`` 类型按 ``fixedX/Y/Z`` 逐分量约束。

        只约束 X 方向时，结构仍有 Y/Z 平移与转动自由，因此必然出现
        接近零的模态；而全约束时没有。这是"逐分量约束真的生效"的证据。
        """
        partial = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=[BoundaryCondition(
                id="roll", name="滚动支承", type="displacement",
                applicationType="face", entityIndex=self.fixed_face.id,
                displacement={"x": 0.0, "y": 0.0, "z": 0.0},
                fixedX=True, fixedY=False, fixedZ=False,
            )],
            faces=self.mesh.faces,
            length_unit="mm",
            num_modes=8,
        )))
        self.assertGreater(
            partial.rigid_body_modes, 0,
            "只约束一个方向后仍应存在未约束的刚体运动",
        )

    def test_nonzero_prescribed_value_is_flagged(self):
        """特征值问题是齐次的：非零"指定位移"必须被明确说明为不参与求解。"""
        result = asyncio.run(solve_modal_impl(ModalRequest(
            geometry_filename=CUBE,
            material_id="structural_steel",
            boundary_conditions=[BoundaryCondition(
                id="move", name="强制位移", type="displacement",
                applicationType="face", entityIndex=self.fixed_face.id,
                displacement={"x": 1.0e-3, "y": 0.0, "z": 0.0},
                fixedX=True, fixedY=True, fixedZ=True,
            )],
            faces=self.mesh.faces,
            length_unit="mm",
            num_modes=3,
        )))
        self.assertTrue(
            any("齐次" in warning for warning in result.warnings),
            f"应说明位移数值不参与特征值问题：{result.warnings}",
        )

    def test_invalid_num_modes_is_rejected(self):
        from fastapi import HTTPException

        for bad in (0, -1, MAX_MODES + 1):
            with self.subTest(num_modes=bad):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(solve_modal_impl(ModalRequest(
                        geometry_filename=CUBE,
                        material_id="structural_steel",
                        boundary_conditions=[self.fixed_bc],
                        faces=self.mesh.faces,
                        num_modes=bad,
                    )))
                self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_material_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_modal_impl(ModalRequest(
                geometry_filename=CUBE,
                material_id="unobtainium",
                boundary_conditions=[self.fixed_bc],
                faces=self.mesh.faces,
            )))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_mesh_is_409(self):
        """没划网格时给出可操作的提示，而不是 500。"""
        import os

        target = config.UPLOAD_DIR / (CUBE + ".msh")
        backup = target.with_suffix(".msh.bak")
        os.replace(target, backup)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(solve_modal_impl(ModalRequest(
                    geometry_filename=CUBE,
                    material_id="structural_steel",
                    boundary_conditions=[],
                )))
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            os.replace(backup, target)

    def test_material_without_density_is_rejected(self):
        """
        自定义材料可能没填密度——必须明确报错，而不是当成 0
        （密度为 0 会让质量矩阵奇异，特征值全部发散）。
        """
        from materials import MATERIALS_DB, Material

        material = Material(id="custom_no_density", name="无密度材料", density=0.0,
                            youngsModulus=2.0e11, poissonsRatio=0.3, color="#FFFFFF",
                            type="custom")
        MATERIALS_DB.append(material)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(solve_modal_impl(ModalRequest(
                    geometry_filename=CUBE,
                    material_id="custom_no_density",
                    boundary_conditions=[self.fixed_bc],
                    faces=self.mesh.faces,
                )))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("密度", ctx.exception.detail)
        finally:
            MATERIALS_DB.remove(material)

    def test_bad_length_unit_is_400(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_modal_impl(ModalRequest(
                geometry_filename=CUBE,
                material_id="structural_steel",
                length_unit="inch",
            )))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_all_dofs_constrained_is_rejected(self):
        """把整个模型都固定住时没有可求的振型，必须明确报错而不是崩溃。"""
        mesh = structured_box(1.0, 1.0, 1.0, 1, 1, 1)
        basis, stiffness, mass = assemble(mesh)
        everything = np.arange(basis.N, dtype=np.int64)
        with self.assertRaises(ValueError):
            solve_generalized_modes(stiffness, mass, everything, num_modes=3)


def _wait_for_job(job_id, timeout=180.0):
    """轮询到任务进入终态，返回 Job。"""
    import time

    from jobs import get_job

    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = get_job(job_id)
        if job is not None and job.status in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没有结束：{job}")


def _drain_job_queue(timeout=180.0):
    """
    等所有任务结束。

    **这不是可有可无的收尾**：gmsh 的状态是进程级全局的，本项目约定所有
    gmsh 操作都在单线程工作器里串行执行（见 `gmsh_session.py` / `jobs.py`）。
    如果某个测试在中途断言失败、把任务留在工作线程里继续跑 gmsh，
    而下一个测试又在主线程里调用 ``gmsh.open``，两边**并发**访问同一个
    gmsh 全局状态，结果是 ``OSError: access violation reading 0x0`` ——
    一个跟失败原因毫无关系的崩溃，会把人往完全错误的方向带。
    （本文件的初版就踩过：一条 ``assertEqual(status, "queued")`` 提前失败，
    导致后面两个测试类以 access violation 报错。）
    """
    import time

    from jobs import list_jobs

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(job.status in ("queued", "running") for job in list_jobs()):
            return
        time.sleep(0.05)


class ModalJobTest(unittest.TestCase):
    """异步任务接口：结果必须与同步接口一致（前端走的就是这条路径）。"""

    def tearDown(self):
        # 无论断言是否失败，都要等队列排空再进入下一个测试（见 _drain_job_queue）
        _drain_job_queue()

    def test_modal_job_matches_sync(self):
        from jobs import submit_modal_job

        mesh = _cube_mesh()
        fixed = _face_with_normal(mesh.faces, 0, -1)
        payload = {
            "geometry_filename": CUBE,
            "material_id": "structural_steel",
            "length_unit": "mm",
            "num_modes": 4,
            "faces": [face.model_dump() for face in mesh.faces],
            "boundary_conditions": [
                {"id": "fix", "name": "固定端", "type": "fixed",
                 "applicationType": "face", "entityIndex": fixed.id},
            ],
        }
        submitted = asyncio.run(submit_modal_job(payload))

        # 先等它跑完，再断言。反过来写的话，一旦断言失败就会把任务留在
        # 工作线程里继续碰 gmsh，后续测试会以 access violation 崩溃。
        job = _wait_for_job(submitted["job_id"])

        # 任务可能已经被工作线程取走，因此 queued / running 都算正常
        self.assertIn(submitted["status"], ("queued", "running"))
        self.assertEqual(submitted["poll"], f"/api/jobs/{submitted['job_id']}")
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(len(job.result["frequencies"]), 4)

        sync = asyncio.run(solve_modal_impl(ModalRequest(**payload)))
        np.testing.assert_allclose(
            job.result["frequencies"], sync.frequencies, rtol=1e-12,
            err_msg="异步任务与同步接口的频率不一致",
        )

    def test_job_rejects_invalid_payload(self):
        from fastapi import HTTPException

        from jobs import submit_modal_job

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(submit_modal_job({"geometry_filename": CUBE}))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("material_id", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
