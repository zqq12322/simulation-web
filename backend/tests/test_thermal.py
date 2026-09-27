"""
稳态热传导测试。

核心是**解析解逐点校验**：一个立方体，两端面给定温度、其余四面绝热
（零热流是弱形式的自然边界条件），此时温度沿轴向**精确线性**：

    T(x) = T0 + (T1 - T0) * (x + L/2) / L
    q    = k * (T1 - T0) / L        （W/m²，方向沿 -x）

这条解析解不需要任何近似假设，因此可以按机器精度断言，而不是"看着差不多"。
"""

import asyncio
import unittest

import numpy as np
from fastapi import HTTPException

import config
from constraints import BoundaryCondition
from geometry import generate_mesh
from thermal import ThermalRequest, solve_thermal_impl

CUBE = "default_cube.step"
STEEL_K = 50.0          # 结构钢热导率 W/(m·K)，见 materials.py
T_COLD = 273.15         # 0 °C
T_HOT = 373.15          # 100 °C
DELTA_T = T_HOT - T_COLD

_MESH_CACHE = {}


def _cube_mesh():
    if "cube" not in _MESH_CACHE:
        _MESH_CACHE["cube"] = asyncio.run(generate_mesh(CUBE, 1.5))
    return _MESH_CACHE["cube"]


def _face(faces, axis, sign):
    for face in faces:
        if face.normal and face.normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


def _solve(length_unit="m", t_cold=T_COLD, t_hot=T_HOT, material_id="structural_steel"):
    mesh = _cube_mesh()
    cold = _face(mesh.faces, 0, -1)
    hot = _face(mesh.faces, 0, +1)

    bcs = [
        BoundaryCondition(id="cold", name="冷端", type="temperature",
                          applicationType="face", entityIndex=cold.id, temperature=t_cold),
        BoundaryCondition(id="hot", name="热端", type="temperature",
                          applicationType="face", entityIndex=hot.id, temperature=t_hot),
    ]
    request = ThermalRequest(
        geometry_filename=CUBE,
        material_id=material_id,
        boundary_conditions=bcs,
        faces=mesh.faces,
        length_unit=length_unit,
    )
    return mesh, asyncio.run(solve_thermal_impl(request))


class SteadyConductionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh, cls.result = _solve("m")

    def test_solved(self):
        self.assertEqual(self.result.status, "solved")
        self.assertEqual(len(self.result.temperatures), len(self.mesh.nodes))

    def test_boundary_temperatures_are_exact(self):
        for index, node in enumerate(self.mesh.nodes):
            if abs(node[0] + 5.0) < 1e-9:
                self.assertAlmostEqual(self.result.temperatures[index], T_COLD, places=9)
            elif abs(node[0] - 5.0) < 1e-9:
                self.assertAlmostEqual(self.result.temperatures[index], T_HOT, places=9)

    def test_temperature_profile_is_exactly_linear(self):
        """
        绝热侧面 ⇒ 一维导热 ⇒ 温度沿 x 精确线性。逐节点与解析解比较。
        """
        length = 10.0
        max_error = 0.0
        for index, (x, _y, _z) in enumerate(self.mesh.nodes):
            expected = T_COLD + DELTA_T * (x + length / 2.0) / length
            max_error = max(max_error, abs(self.result.temperatures[index] - expected))
        self.assertLess(max_error, 1e-9, f"温度偏离解析线性解 {max_error:.3e} K")

    def test_heat_flux_matches_fourier_law(self):
        """q = k·ΔT/L —— 一维稳态导热的热流是常数。"""
        expected = STEEL_K * DELTA_T / 10.0        # L = 10 m（按 m 解释）
        self.assertAlmostEqual(
            self.result.max_heat_flux, expected, delta=expected * 1e-6,
            msg=f"max|q|={self.result.max_heat_flux:.6g}，解析 {expected:.6g}",
        )

    def test_min_max_temperature(self):
        self.assertAlmostEqual(self.result.min_temperature, T_COLD, places=9)
        self.assertAlmostEqual(self.result.max_temperature, T_HOT, places=9)

    def test_declares_si_units(self):
        self.assertEqual(self.result.units["temperature"], "K")
        self.assertEqual(self.result.units["heat_flux"], "W/m^2")


class ThermalUnitConsistencyTest(unittest.TestCase):
    """长度单位只影响热流的量级（q ∝ 1/L），温度场不受影响。"""

    def test_length_unit_scales_heat_flux_only(self):
        _mesh_m, meters = _solve("m")
        _mesh_mm, millimeters = _solve("mm")

        # 同一个几何按 mm 解释 ⇒ 长度缩小 1000 倍 ⇒ 热流放大 1000 倍
        ratio = millimeters.max_heat_flux / meters.max_heat_flux
        self.assertAlmostEqual(ratio, 1000.0, delta=1e-6)

        # 温度分布不变（温差与几何比例都不受单位影响）
        for a, b in zip(meters.temperatures, millimeters.temperatures):
            self.assertAlmostEqual(a, b, places=9)


class ThermalValidationTest(unittest.TestCase):
    def test_missing_temperature_bc_is_rejected(self):
        """只有绝热边界时温度场不唯一（矩阵奇异），必须明确报错而不是算个瞎结果。"""
        mesh = _cube_mesh()
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_thermal_impl(ThermalRequest(
                geometry_filename=CUBE, material_id="structural_steel",
                boundary_conditions=[], faces=mesh.faces,
            )))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("温度边界条件", ctx.exception.detail)

    def test_structural_bcs_are_warned_not_silently_ignored(self):
        mesh = _cube_mesh()
        cold = _face(mesh.faces, 0, -1)
        hot = _face(mesh.faces, 0, +1)
        result = asyncio.run(solve_thermal_impl(ThermalRequest(
            geometry_filename=CUBE, material_id="structural_steel",
            faces=mesh.faces,
            boundary_conditions=[
                BoundaryCondition(id="cold", name="冷端", type="temperature",
                                  applicationType="face", entityIndex=cold.id,
                                  temperature=T_COLD),
                BoundaryCondition(id="hot", name="热端", type="temperature",
                                  applicationType="face", entityIndex=hot.id,
                                  temperature=T_HOT),
                # 结构类的边界条件在热分析里没有意义，必须给出警告
                BoundaryCondition(id="fix", name="固定端", type="fixed",
                                  applicationType="face", entityIndex=cold.id),
            ],
        )))
        self.assertTrue(any("fixed" in w for w in result.warnings), result.warnings)

    def test_material_without_conductivity_is_rejected(self):
        """自定义材料可能没有热导率——必须明确报错，而不是当成 0（除以 0）。"""
        from materials import Material, MATERIALS_DB

        material = Material(id="custom_no_k", name="无热导率材料", density=1000,
                            youngsModulus=1e9, poissonsRatio=0.3, color="#FFFFFF",
                            type="custom")
        MATERIALS_DB.append(material)
        try:
            mesh = _cube_mesh()
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(solve_thermal_impl(ThermalRequest(
                    geometry_filename=CUBE, material_id="custom_no_k", faces=mesh.faces,
                    boundary_conditions=[BoundaryCondition(
                        id="c", name="冷端", type="temperature",
                        applicationType="face",
                        entityIndex=_face(mesh.faces, 0, -1).id, temperature=T_COLD)],
                )))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("热导率", ctx.exception.detail)
        finally:
            MATERIALS_DB.remove(material)

    def test_unknown_material_returns_404(self):
        mesh = _cube_mesh()
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_thermal_impl(ThermalRequest(
                geometry_filename=CUBE, material_id="does-not-exist", faces=mesh.faces,
            )))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_mesh_returns_409(self):
        """没划网格时给出可操作的提示，而不是 500。"""
        import os

        target = config.UPLOAD_DIR / "default_cube.step.msh"
        backup = target.with_suffix(".msh.bak")
        os.replace(target, backup)
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(solve_thermal_impl(ThermalRequest(
                    geometry_filename=CUBE, material_id="structural_steel",
                )))
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            os.replace(backup, target)

    def test_bad_length_unit_returns_400(self):
        mesh = _cube_mesh()
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(solve_thermal_impl(ThermalRequest(
                geometry_filename=CUBE, material_id="structural_steel",
                faces=mesh.faces, length_unit="inch",
            )))
        self.assertEqual(ctx.exception.status_code, 400)


class MaterialConductivityTest(unittest.TestCase):
    def test_builtin_materials_have_conductivity(self):
        from materials import MATERIALS_DB

        for material in MATERIALS_DB:
            with self.subTest(material=material.id):
                self.assertIsNotNone(
                    material.thermalConductivity,
                    f"内置材料 {material.id} 缺少热导率",
                )
                self.assertGreater(material.thermalConductivity, 0)

    def test_conductivity_values_are_physically_sane(self):
        from materials import MATERIALS_DB

        by_id = {m.id: m.thermalConductivity for m in MATERIALS_DB}
        # 铜 > 铝 > 钢 > 钛 > 塑料，这是材料手册里的常识排序
        self.assertGreater(by_id["copper"], by_id["aluminum_alloy"])
        self.assertGreater(by_id["aluminum_alloy"], by_id["structural_steel"])
        self.assertGreater(by_id["structural_steel"], by_id["titanium"])
        self.assertGreater(by_id["titanium"], by_id["abs_plastic"])


class ThermalJobTest(unittest.TestCase):
    """异步任务接口：结果必须与同步接口一致。"""

    def test_thermal_job_matches_sync(self):
        import time

        from jobs import get_job, submit_thermal_job

        mesh = _cube_mesh()
        cold = _face(mesh.faces, 0, -1)
        hot = _face(mesh.faces, 0, +1)
        payload = {
            "geometry_filename": CUBE,
            "material_id": "structural_steel",
            "length_unit": "m",
            "faces": [f.model_dump() for f in mesh.faces],
            "boundary_conditions": [
                {"id": "cold", "name": "冷端", "type": "temperature",
                 "applicationType": "face", "entityIndex": cold.id, "temperature": T_COLD},
                {"id": "hot", "name": "热端", "type": "temperature",
                 "applicationType": "face", "entityIndex": hot.id, "temperature": T_HOT},
            ],
        }
        submitted = asyncio.run(submit_thermal_job(payload))

        deadline = time.time() + 120
        job = None
        while time.time() < deadline:
            job = get_job(submitted["job_id"])
            if job is not None and job.status in ("succeeded", "failed"):
                break
            time.sleep(0.05)

        self.assertIsNotNone(job)
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertAlmostEqual(
            job.result["max_temperature"], T_HOT, places=9
        )


if __name__ == "__main__":
    unittest.main()
