"""
模态分析（自由振动特征值问题）。

物理
----
无阻尼自由振动满足 ``M ü + K u = 0``，令 ``u = φ e^{iωt}`` 得到**广义特征值问题**

    K φ = λ M φ,      λ = ω²

其中 ``K`` 是线弹性刚度矩阵、``M`` 是**一致质量矩阵** ``ρ∫ N_i·N_j dV``。
特征值给出固有频率 ``f = √λ / (2π)``，特征向量给出**振型**。

这里只有两处物理输入：刚度（``E``、``ν``）与密度 ``ρ``。**载荷不参与**——
线性模态分析的固有频率与载荷幅值无关，这一点必须显式告诉用户，
否则"我加了 1000 N，频率怎么没变"会变成一个反复出现的疑问。

边界条件
--------
只有**约束类**边界条件有意义（``fixed`` / ``displacement``）：它们把自由度从
特征值问题里剔除（齐次 Dirichlet）。**位移的数值大小无关紧要**——特征值问题
是齐次的，只取"哪些自由度被约束"这一信息。``force`` / ``pressure`` /
``temperature`` 会被忽略并给出警告。

完全不加约束是**合法**的：自由-自由结构有 6 个刚体模态（3 平移 + 3 转动），
频率为 0。这时刚度矩阵奇异，特征值求解必须用负 shift 的 shift-invert
（见 `_shift_for` 的说明），并且结果里会报告 ``rigid_body_modes`` 的数量。

验证
----
`tests/test_modal.py` 分四层做解析校验（都不是"结果有限"这种弱断言）：

1. **刚体模态精确性**：对任意网格，6 个解析刚体模态必须满足 ``K u = 0``
   与 ``uᵀMu`` = 解析惯量（平移 ``ρV``、转动 ``ρV(w²+h²)/12``），
   误差在机器精度量级（实测 1e-16）；
2. **自由-自由**：特征值里恰好有 6 个"零"，其后才是弹性模态；
3. **侧限杆轴向振动**：全域滚动支承（``u_y = u_z = 0``）的细杆，其解析频率是
   ``f_n = (2n-1)/(4L)·√((λ+2μ)/ρ)``——注意**不是** ``√(E/ρ)``，因为材料
   不能横向收缩，有效模量是侧限模量 ``E(1-ν)/((1+ν)(1-2ν))``
   （ν=0.3 时高出 16%，正好是"用错模量"会出现的偏差）。实测二阶收敛：
   误差 6.4e-3 → 1.6e-3 → 3.9e-4 → 4e-5（nx=4/8/16/32）；
4. **单位不变性**：同一份网格按 m 与 mm 解释，频率必须**完全相同**
   （频率只依赖 E/ρ 与几何比例，与长度单位无关）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.sparse.linalg as spla
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from skfem import Basis, BilinearForm, ElementTetP1, ElementVectorH1, MeshTet, asm
from skfem.helpers import dot
from skfem.models.elasticity import lame_parameters, linear_elasticity

from config import length_scale_to_meter, resolve_upload_path, validate_length_unit
from constraints import BoundaryCondition
from fe_utils import (
    compute_model_span,
    load_tet_mesh_from_msh,
    parse_displacement,
    resolve_target_nodes,
)
from geometry import FaceInfo
from jobs import run_in_worker
from logging_config import get_logger
from materials import MATERIALS_DB

logger = get_logger(__name__)
router = APIRouter()

#: 模态分析里有意义的边界条件类型（都是约束类）
SUPPORTED_TYPES = {"fixed", "displacement"}

#: 允许求的模态数上限（模态数越多，ARPACK 越慢，前端也显示不下）
MIN_MODES = 1
MAX_MODES = 30
DEFAULT_MODES = 6

#: 返回振型的总分量数上限。振型是 ``3 × 节点数 × 模态数``，
#: 10 万节点 × 6 阶就是 180 万个数，足以把任务表撑爆，
#: 因此超过上限时只返回诊断信息并给出警告（频率仍然全部返回）。
MAX_MODE_SHAPE_VALUES = 6_000_000

#: 判定"零频/刚体模态"的相对阈值：λ_i / λ_max 小于它就认为是刚体模态。
#: 实测自由-自由时零模态约 1e-16·λ_max，第一个弹性模态约 1e-4·λ_max，
#: 两者之间隔了十几个数量级，阈值很安全。
RIGID_MODE_RELATIVE_TOLERANCE = 1e-8


@BilinearForm
def vector_mass(u, v, _):
    """
    一致质量矩阵的被积函数 ``N_i·N_j``。

    ``skfem.models.poisson.mass`` 写的是 ``u * v``，对 ``ElementVectorH1``
    的插值（形如 ``(3, n_qp, n_elem)``）**不做分量缩并**，会直接抛
    ``could not broadcast input array from shape (3,4) into shape (6,)``。
    向量单元必须显式缩并分量，故用 ``dot`` 自己定义。
    """
    return dot(u, v)


class ModalRequest(BaseModel):
    """模态分析请求。"""

    geometry_filename: str
    material_id: str
    boundary_conditions: List[BoundaryCondition] = []
    faces: List[FaceInfo] = []
    length_unit: Optional[str] = None
    #: 要求的模态数（从最低频开始）
    num_modes: int = DEFAULT_MODES


class ModalResult(BaseModel):
    status: str
    message: str
    #: 固有频率，单位 Hz，按升序排列
    frequencies: List[float]
    #: 角频率 ω = 2πf，单位 rad/s
    angular_frequencies: List[float]
    #: 特征值 λ = ω²，单位 1/s²（便于核对与复算）
    eigenvalues: List[float]
    #: 振型：mode_shapes[i][node] = [ux, uy, uz]，每个振型已按最大位移归一化到 1
    mode_shapes: List[List[List[float]]] = []
    #: 频率 ≈ 0 的模态个数（自由-自由结构应为 6）
    rigid_body_modes: int = 0
    #: 未约束自由度数
    num_free_dofs: int = 0
    warnings: List[str] = []
    units: Dict[str, str] = {
        "length": "m",
        "frequency": "Hz",
        "angular_frequency": "rad/s",
        "density": "kg/m^3",
    }
    length_unit: str = "m"


# --------------------------------------------------------------------- 数值核心


def _shift_for(stiffness, mass) -> float:
    """
    为 shift-invert 选一个**负的** shift ``σ = -α``（α > 0）。

    为什么不能用 ``σ = 0``
    ----------------------
    ``eigsh(K, M=M, sigma=0)`` 等价于对 ``K`` 做分解，而自由-自由结构的 ``K``
    **本来就是奇异的**（刚体模态就是它的零空间）。实测在奇异矩阵上 ``σ=0``
    虽然有时能返回结果，但并不保证可靠。

    取 ``σ = -α``（α>0）时分解的是 ``K + αM``，它对任意 α>0 都是**正定**的，
    于是刚体模态不再是零空间、分解一定成功；``which="LM"`` 作用在
    ``(K + αM)⁻¹M`` 上，特征值 ``ν = 1/(λ + α)`` 最大的恰好对应 λ 最小的，
    因此返回的仍是最低阶模态。这一点与 α 的大小无关。

    为什么 α 要取小
    ---------------
    ``λ = 1/ν - α`` 存在相减抵消：α 越大，λ 的相对精度损失越大。但 α 太小又会让
    ``K + αM`` 接近奇异（自由-自由时它的最小特征值就是 α）。取
    ``α = trace(K)/trace(M) · 1e-6``——实测自由-自由（λ₇ ≈ 1.3e7，
    α ≈ 4.6e5）能稳定得到 6 个零模态；而 α 放大到 0.1·trace 比值时会**丢掉
    一个零模态**（1.4e-4 直接跳到 1.3e7）。小 shift 是必要的，不是保守。
    """
    diagonal_scale = float(np.sum(stiffness.diagonal()) / np.sum(mass.diagonal()))
    if not np.isfinite(diagonal_scale) or diagonal_scale <= 0.0:
        diagonal_scale = 1.0
    return -diagonal_scale * 1e-6


def solve_generalized_modes(
    stiffness,
    mass,
    constrained_dofs,
    num_modes: int,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    求解 ``K φ = λ M φ``（核心，可脱离 gmsh / 文件系统单独测试）。

    Parameters
    ----------
    stiffness, mass
        稀疏刚度矩阵与一致质量矩阵（同尺寸）。
    constrained_dofs
        **齐次** Dirichlet 自由度的集合。特征值问题是齐次的，
        因此只需要"哪些自由度被约束"，被约束的值本身没有意义。
    num_modes
        要求的模态数（会按可用自由度数截断）。

    Returns
    -------
    (eigenvalues, mode_vectors, info)
        ``eigenvalues`` 升序；``mode_vectors`` 形状 ``(n_dof, n_modes)``，
        已展开回全长（被约束的自由度为 0）；``info`` 含诊断信息。
    """
    n_dof = stiffness.shape[0]
    constrained = np.unique(np.asarray(constrained_dofs, dtype=np.int64))
    free = np.setdiff1d(np.arange(n_dof, dtype=np.int64), constrained)

    if len(free) < 2:
        raise ValueError(
            "所有自由度都被约束了，没有可求解的振型——请减少约束条件"
        )

    # eigsh 要求 k < N，留一点余量
    k = int(min(num_modes, len(free) - 1))
    truncated = k < int(num_modes)

    condensed_k = stiffness[free][:, free]
    condensed_m = mass[free][:, free]
    # 转成 CSC：SuperLU 内部就是按列存的，交给它 CSR 会触发
    # "Transforming over N elements to C_CONTIGUOUS" 的整块拷贝警告。
    condensed_k = condensed_k.tocsc()
    condensed_m = condensed_m.tocsc()
    shift = _shift_for(condensed_k, condensed_m)

    # 固定初始向量：ARPACK 默认用随机向量，会让同一次求解的结果不可复现，
    # 而"结果不可复现"的测试等于没有测试。
    v0 = np.random.default_rng(0).random(len(free), dtype=np.float64)

    converged = True
    try:
        eigenvalues, eigenvectors = spla.eigsh(
            condensed_k, k=k, M=condensed_m, sigma=shift, which="LM", v0=v0
        )
    except spla.ArpackNoConvergence as exc:
        # 部分收敛也返回，但必须如实告知（不能让用户以为拿到了全部模态）
        eigenvalues = np.asarray(exc.eigenvalues, dtype=np.float64)
        eigenvectors = np.asarray(exc.eigenvectors, dtype=np.float64)
        converged = False
        logger.warning("模态求解未完全收敛，返回已收敛的 %d 阶", len(eigenvalues))

    order = np.argsort(eigenvalues)
    eigenvalues = np.asarray(eigenvalues, dtype=np.float64)[order]
    eigenvectors = np.asarray(eigenvectors, dtype=np.float64)[:, order]

    mode_vectors = np.zeros((n_dof, len(eigenvalues)), dtype=np.float64)
    mode_vectors[free] = eigenvectors

    info = {
        "num_free_dofs": int(len(free)),
        "num_constrained_dofs": int(len(constrained)),
        "num_modes_requested": int(num_modes),
        "num_modes_returned": int(len(eigenvalues)),
        "modes_truncated": bool(truncated),
        "shift": float(shift),
        "converged": bool(converged),
    }
    return eigenvalues, mode_vectors, info


def _normalize_mode_shape(mode: np.ndarray, basis) -> np.ndarray:
    """
    把振型按"最大节点位移 = 1"缩放，返回 ``(n_nodes, 3)``。

    特征向量本身只有方向有意义（幅值任意）；按最大值归一化后，
    前端拿到的是"形状"，可以统一乘上自己的放大系数显示。
    """
    components = np.stack(
        [mode[basis.nodal_dofs[axis]] for axis in range(3)], axis=1
    )
    magnitude = np.linalg.norm(components, axis=1)
    peak = float(magnitude.max()) if magnitude.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        return np.zeros_like(components)
    return components / peak


def _classify_rigid_modes(eigenvalues: np.ndarray) -> int:
    """数出频率≈0 的刚体模态个数（判据见 RIGID_MODE_RELATIVE_TOLERANCE）。"""
    if eigenvalues.size == 0:
        return 0
    scale = float(np.max(np.abs(eigenvalues)))
    if scale <= 0.0:
        return int(eigenvalues.size)
    return int(np.count_nonzero(np.abs(eigenvalues) < RIGID_MODE_RELATIVE_TOLERANCE * scale))


# --------------------------------------------------------------------- 端点实现


async def solve_modal_impl(request: ModalRequest) -> ModalResult:
    """模态求解（实现，在后台工作线程中执行，见 jobs.py）。"""
    try:
        length_unit = validate_length_unit(request.length_unit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    length_scale = length_scale_to_meter(length_unit)

    if not (MIN_MODES <= int(request.num_modes) <= MAX_MODES):
        raise HTTPException(
            status_code=400,
            detail=f"num_modes 必须在 [{MIN_MODES}, {MAX_MODES}] 之间，收到 {request.num_modes}",
        )

    try:
        file_path = str(resolve_upload_path(request.geometry_filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    material = next((m for m in MATERIALS_DB if m.id == request.material_id), None)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")

    density = getattr(material, "density", None)
    if density is None or density <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"材料「{material.name}」没有定义密度 density，无法做模态分析",
        )

    msh_path = file_path + ".msh"
    if not os.path.exists(msh_path):
        raise HTTPException(
            status_code=409,
            detail="尚未生成网格：请先调用 POST /api/generate-mesh 或 /api/jobs/generate-mesh",
        )

    mesh, face_triangles = load_tet_mesh_from_msh(msh_path)
    if length_scale != 1.0:
        mesh = MeshTet(np.ascontiguousarray(mesh.p * length_scale), mesh.t)

    span = compute_model_span(mesh)
    basis = Basis(mesh, ElementVectorH1(ElementTetP1()))

    youngs = float(material.youngsModulus)
    poisson = float(material.poissonsRatio)
    lam, mu = lame_parameters(youngs, poisson)

    stiffness = asm(linear_elasticity(lam, mu), basis)
    mass = float(density) * asm(vector_mass, basis)

    # ---- 约束自由度（齐次 Dirichlet） --------------------------------------
    constrained: List[int] = []
    warnings: List[str] = []

    for bc in request.boundary_conditions:
        if bc.type not in SUPPORTED_TYPES:
            message = (
                f"模态分析忽略边界条件「{bc.name}」（类型 '{bc.type}'）："
                "固有频率与振型只由质量与刚度决定，与载荷大小无关；"
                "只有固定约束 / 强制位移能改变模态"
            )
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
            message = f"边界条件「{bc.name}」没有选中任何节点，已忽略"
            logger.warning(message)
            warnings.append(message)
            continue

        if bc.type == "fixed":
            components = (0, 1, 2)
        else:
            values = parse_displacement(bc)
            flags = (
                getattr(bc, "fixedX", None),
                getattr(bc, "fixedY", None),
                getattr(bc, "fixedZ", None),
            )
            if all(flag is None for flag in flags):
                flags = (True, True, True)
            components = tuple(axis for axis in range(3) if flags[axis])
            if not components:
                message = f"强制位移「{bc.name}」没有勾选任何约束方向，已忽略"
                logger.warning(message)
                warnings.append(message)
                continue
            if any(abs(value) > 0.0 for value in values):
                # 齐次特征值问题里，"位移值"没有意义——必须说清楚，
                # 否则用户会以为自己在施加一个非零预变形。
                message = (
                    f"模态分析按**齐次**边界条件处理「{bc.name}」："
                    f"只约束方向 {components}，位移数值 {values} 不参与特征值问题"
                )
                logger.info(message)
                warnings.append(message)

        for axis in components:
            constrained.extend(basis.nodal_dofs[axis][indices].tolist())

    try:
        eigenvalues, mode_vectors, info = solve_generalized_modes(
            stiffness, mass, constrained, int(request.num_modes)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # 数值分解失败要给出可操作的原因，而不是 500 堆栈
        logger.exception("模态特征值求解失败")
        raise HTTPException(
            status_code=400,
            detail=f"模态求解失败（可能是约束不足或网格质量过差）：{exc}",
        )

    if info["modes_truncated"]:
        message = (
            f"要求的 {info['num_modes_requested']} 阶模态超出可求解范围，"
            f"只返回了 {info['num_modes_returned']} 阶"
        )
        logger.warning(message)
        warnings.append(message)
    if not info["converged"]:
        warnings.append(
            "特征值迭代未完全收敛，返回的是已收敛的部分模态；"
            "结果可能缺少高阶模态，建议加密网格或减少模态数"
        )

    # 特征值里可能出现极小的负数（刚体模态的数值残差），开方前必须截断
    eigenvalues = np.where(np.isfinite(eigenvalues), eigenvalues, 0.0)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    angular = np.sqrt(eigenvalues)
    frequencies = angular / (2.0 * np.pi)

    rigid_body_modes = _classify_rigid_modes(eigenvalues)
    if rigid_body_modes > 0:
        message = (
            f"检测到 {rigid_body_modes} 个频率≈0 的刚体模态："
            "结构未被充分约束（自由模态）。若只想看弹性模态，请补充固定约束"
        )
        logger.warning(message)
        warnings.append(message)

    shape_budget = 3 * basis.nodal_dofs.shape[1] * max(len(eigenvalues), 1)
    mode_shapes: List[List[List[float]]] = []
    if shape_budget <= MAX_MODE_SHAPE_VALUES:
        mode_shapes = [
            _normalize_mode_shape(mode_vectors[:, index], basis).tolist()
            for index in range(mode_vectors.shape[1])
        ]
    else:
        warnings.append(
            f"模型过大（{basis.nodal_dofs.shape[1]} 个节点 × {len(eigenvalues)} 阶），"
            "已省略振型数据；频率与特征值不受影响"
        )

    logger.info(
        "模态分析完成：ρ=%.6g, 节点 %d, 自由 DOF %d, 刚体模态 %d, f1=%.6g Hz",
        density, basis.nodal_dofs.shape[1], info["num_free_dofs"],
        rigid_body_modes, float(frequencies[0]) if len(frequencies) else 0.0,
    )

    return ModalResult(
        status="solved",
        message="Modal analysis completed successfully.",
        frequencies=[float(value) for value in frequencies],
        angular_frequencies=[float(value) for value in angular],
        eigenvalues=[float(value) for value in eigenvalues],
        mode_shapes=mode_shapes,
        rigid_body_modes=rigid_body_modes,
        num_free_dofs=info["num_free_dofs"],
        warnings=warnings,
        length_unit=length_unit,
    )


@router.post("/modal/solve", response_model=ModalResult)
async def solve_modal(request: ModalRequest):
    """
    模态分析（同步接口）。

    大模型建议改用 POST /api/jobs/modal（提交后轮询，避免请求超时）。
    """
    return await run_in_worker(solve_modal_impl, request=request)
