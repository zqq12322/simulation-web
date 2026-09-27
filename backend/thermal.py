"""
稳态热传导（linear steady-state heat conduction）。

物理
----
无内热源的稳态温度场满足 ``∇·(k∇T) = 0``，弱形式为 ``∫ k ∇T·∇v dV = 0``。

边界条件
--------
- **面上给定温度**（Dirichlet / 第一类边界条件）——前端用 `temperature` 类型的
  边界条件表达；
- **未指定的面天然绝热**（自然边界条件，零热流）——这是弱形式的自然结果，
  不需要额外处理。也正是这一点，让「一个立方体两端定温、其余面绝热」的算例
  有**精确解析解**：温度沿轴向线性分布，热流 ``q = k·ΔT/L``。

单位约定（与结构求解器一致）
--------------------------
坐标按 `length_unit` 换算成米；**温度用开尔文**；材料 ``thermalConductivity``
用 W/(m·K)；因此热流单位是 W/m²。前端负责 °C ↔ K 的换算。
（纯导热只依赖温差，所以偏移量不影响结果，但显式用 K 可以避免以后加
对流/辐射边界时出现单位混乱。）

验证
----
`tests/test_thermal.py` 用「立方体两端定温」的解析解做逐点校验：
温度必须精确线性、热流必须等于 ``k·ΔT/L``。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from skfem import Basis, ElementTetP1, asm, condense, solve
from skfem.models.poisson import laplace

import os

from config import length_scale_to_meter, resolve_upload_path, validate_length_unit
from constraints import BoundaryCondition
from fe_utils import (
    compute_model_span,
    load_tet_mesh_from_msh,
    resolve_target_nodes,
)
from geometry import FaceInfo
from gmsh_session import start_model  # noqa: F401  (供未来扩展/一致性)
from jobs import run_in_worker
from logging_config import get_logger
from materials import MATERIALS_DB

logger = get_logger(__name__)
from auth import require_user

#: 整个 router 都要求登录。用**路由级依赖**而不是给每个端点加参数：
#: 端点本身并不需要知道「你是谁」，而且 40 多个既有测试是**直接调用端点函数**的
#: （不经 HTTP），逐个加参数会让它们全部失效。
router = APIRouter(dependencies=[Depends(require_user)])



class ThermalRequest(BaseModel):
    """稳态热传导求解请求。"""

    geometry_filename: str
    material_id: str
    boundary_conditions: List[BoundaryCondition] = []
    faces: List[FaceInfo] = []
    length_unit: Optional[str] = None


class ThermalResult(BaseModel):
    status: str
    message: str
    #: 每个节点的温度，单位 K
    temperatures: List[float]
    min_temperature: float
    max_temperature: float
    #: 最大热流密度 W/m²
    max_heat_flux: float
    #: 被忽略/降级的边界条件说明
    warnings: List[str] = []
    units: Dict[str, str] = {
        "length": "m",
        "temperature": "K",
        "heat_flux": "W/m^2",
        "thermal_conductivity": "W/(m*K)",
    }
    length_unit: str = "m"


def _temperature_kelvin(bc: BoundaryCondition) -> Optional[float]:
    """取温度值（K）；兼容历史写法 ``value``。"""
    for candidate in (getattr(bc, "temperature", None), getattr(bc, "value", None)):
        if isinstance(candidate, (int, float)):
            return float(candidate)
        if isinstance(candidate, dict) and "value" in candidate:
            return float(candidate["value"])
    return None


async def solve_thermal_impl(request: ThermalRequest) -> ThermalResult:
    """稳态热传导求解（实现，在后台工作线程中执行，见 jobs.py）。"""
    try:
        length_unit = validate_length_unit(request.length_unit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    length_scale = length_scale_to_meter(length_unit)

    try:
        file_path = str(resolve_upload_path(request.geometry_filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    material = next((m for m in MATERIALS_DB if m.id == request.material_id), None)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")

    conductivity = material.thermalConductivity
    if conductivity is None or conductivity <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"材料「{material.name}」没有定义热导率 thermalConductivity，"
                "无法做热传导分析"
            ),
        )

    msh_path = file_path + ".msh"
    if not os.path.exists(msh_path):
        raise HTTPException(
            status_code=409,
            detail="尚未生成网格：请先调用 POST /api/generate-mesh 或 /api/jobs/generate-mesh",
        )

    mesh, face_triangles = load_tet_mesh_from_msh(msh_path)
    if length_scale != 1.0:
        from skfem import MeshTet

        mesh = MeshTet(np.ascontiguousarray(mesh.p * length_scale), mesh.t)

    span = compute_model_span(mesh)
    basis = Basis(mesh, ElementTetP1())

    # 导热矩阵：∫ k ∇T·∇v dV
    stiffness = conductivity * asm(laplace, basis)

    prescribed: Dict[int, float] = {}
    warnings: List[str] = []

    temperature_bcs = [bc for bc in request.boundary_conditions if bc.type == "temperature"]
    unsupported = [bc for bc in request.boundary_conditions if bc.type != "temperature"]
    for bc in unsupported:
        message = (
            f"热传导分析忽略边界条件「{bc.name}」（类型 '{bc.type}'）："
            "目前只支持温度边界条件，未指定的面按绝热处理"
        )
        logger.warning(message)
        warnings.append(message)

    for bc in temperature_bcs:
        value = _temperature_kelvin(bc)
        if value is None:
            message = f"温度边界条件「{bc.name}」没有给出温度值，已忽略"
            logger.warning(message)
            warnings.append(message)
            continue

        indices, _areas, locate_error = resolve_target_nodes(
            bc, mesh, face_triangles, request.faces, length_scale, span
        )
        if locate_error:
            logger.warning(locate_error)
            warnings.append(locate_error)
            continue
        if len(indices) == 0:
            message = f"温度边界条件「{bc.name}」没有选中任何节点，已忽略"
            logger.warning(message)
            warnings.append(message)
            continue

        for node_index in indices:
            prescribed[int(basis.nodal_dofs[0][node_index])] = value

        logger.debug(
            "温度 %.4g K 作用于 %s 的 %d 个节点", value, bc.entityIndex, len(indices)
        )

    if not prescribed:
        raise HTTPException(
            status_code=400,
            detail=(
                "至少需要一个温度边界条件：只有零热流的自然边界时，"
                "温度场不唯一（矩阵奇异），问题没有唯一解"
            ),
        )

    prescribed_values = np.zeros(basis.N, dtype=np.float64)
    for dof, value in prescribed.items():
        prescribed_values[dof] = value

    D = np.array(sorted(prescribed), dtype=np.int64)
    temperatures = solve(
        *condense(stiffness, np.zeros(basis.N), x=prescribed_values, D=D)
    )

    # 后处理：q = -k ∇T，投影到节点取最大值
    temperature_field = basis.interpolate(temperatures)
    gradient_qp = temperature_field.grad
    flux_qp = conductivity * np.sqrt(np.einsum('i...,i...->...', gradient_qp, gradient_qp))
    flux_nodal = basis.project(flux_qp)

    values = np.asarray(temperatures, dtype=np.float64)
    values = np.where(np.isfinite(values), values, 0.0)
    flux_values = np.asarray(flux_nodal, dtype=np.float64)
    flux_values = np.where(np.isfinite(flux_values), flux_values, 0.0)

    logger.info(
        "热传导求解完成：k=%.6g, 节点 %d, T∈[%.4g, %.4g] K, max|q|=%.6g W/m²",
        conductivity, basis.N, values.min(), values.max(), float(flux_values.max()),
    )

    return ThermalResult(
        status="solved",
        message="Steady-state thermal analysis completed successfully.",
        temperatures=[float(value) for value in values],
        min_temperature=float(values.min()),
        max_temperature=float(values.max()),
        max_heat_flux=float(flux_values.max()),
        warnings=warnings,
        length_unit=length_unit,
    )


@router.post("/thermal/solve", response_model=ThermalResult)
async def solve_thermal(request: ThermalRequest):
    """
    稳态热传导求解（同步接口）。

    大模型建议改用 POST /api/jobs/thermal（提交后轮询，避免请求超时）。
    """
    return await run_in_worker(solve_thermal_impl, request=request)
