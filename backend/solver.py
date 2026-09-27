from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import numpy as np
from skfem import *
from skfem.helpers import dot, grad, trace, sym_grad, eye, identity, ddot
from skfem.models.elasticity import linear_elasticity, linear_stress, lame_parameters
import os
import gmsh

# Import local modules
from config import resolve_upload_path, validate_length_unit, length_scale_to_meter
from fe_utils import (
    compute_model_span,
    load_tet_mesh_from_msh,   # 由 fe_utils 提供，这里再导出以兼容既有调用方式
    nodal_tributary_areas,
    parse_displacement,
    parse_force,
    resolve_target_nodes,
)
from gmsh_session import ensure_initialized as _ensure_gmsh, open_model_file, start_model
from jobs import run_in_worker
from logging_config import get_logger
from geometry import FaceInfo
from materials import MATERIALS_DB
from constraints import BoundaryCondition

from auth import require_user

#: 整个 router 都要求登录。用**路由级依赖**而不是给每个端点加参数：
#: 端点本身并不需要知道「你是谁」，而且 40 多个既有测试是**直接调用端点函数**的
#: （不经 HTTP），逐个加参数会让它们全部失效。
router = APIRouter(dependencies=[Depends(require_user)])

logger = get_logger(__name__)


def _parse_force(force) -> tuple:
    """
    把前端传来的力（dict / Vector3 / list）统一成 ``(fx, fy, fz)``。

    实现在 ``fe_utils.parse_force``（模态求解器也要用同一套解析规则），
    这里保留同名别名以兼容既有调用与测试。
    """
    return parse_force(force)


def _parse_pressure(bc) -> float:
    """取压力值：优先 ``pressure`` 字段，兼容历史写法 ``value``。"""
    for candidate in (getattr(bc, "pressure", None), getattr(bc, "value", None)):
        if candidate is None:
            continue
        if isinstance(candidate, (int, float)):
            return float(candidate)
        if isinstance(candidate, dict) and "value" in candidate:
            return float(candidate["value"])
    return 0.0


def _parse_displacement(bc) -> tuple:
    """取强制位移值 ``(ux, uy, uz)``（dict / Vector3 / list 均可）。"""
    return parse_displacement(bc)


def surface_normal_from_triangles(points: np.ndarray, triangles: np.ndarray,
                                  reference_point: np.ndarray) -> np.ndarray | None:
    """
    由三角形算出面的面积加权法向，并统一指向 ``reference_point`` 的外侧。

    ``reference_point`` 通常取模型形心。平面面得到的是精确法向；曲面面各三角形
    法向会互相抵消，此时返回 ``None``（调用方应给出警告而不是瞎猜）。
    """
    if triangles.size == 0:
        return None

    tri_pts = points[:, triangles]
    p0, p1, p2 = tri_pts[:, :, 0], tri_pts[:, :, 1], tri_pts[:, :, 2]
    normals = np.cross((p1 - p0).T, (p2 - p0).T)   # 未归一化，模长 = 2*面积
    accumulated = normals.sum(axis=0)

    length = float(np.linalg.norm(accumulated))
    if length < 1e-12:
        return None

    normal = accumulated / length

    face_centre = points[:, np.unique(triangles)].mean(axis=1)
    if float(np.dot(normal, face_centre - reference_point)) < 0.0:
        normal = -normal

    return normal


class SolverRequest(BaseModel):
    geometry_filename: str
    material_id: str
    boundary_conditions: List[BoundaryCondition]
    faces: List[FaceInfo] = [] # Optional face metadata from frontend
    #: 几何坐标的长度单位（"m" 或 "mm"）。内部会换算成米再求解，
    #: 因此结果始终是 SI：位移 m、应力 Pa。
    length_unit: Optional[str] = None

class SolverResult(BaseModel):
    status: str
    message: str
    max_displacement: float
    max_stress: float
    displacements: List[List[float]] # [dx, dy, dz] per node
    stresses: List[float] # Von Mises stress per node
    reaction_forces: Dict[str, List[float]] # { "node_index": [fx, fy, fz] }
    #: 被忽略或降级处理的边界条件说明（前端应展示给用户，避免"静默错误结果"）
    warnings: List[str] = []
    #: 返回值的单位约定（结果一律为 SI，便于与材料库自洽）
    units: Dict[str, str] = {
        "length": "m",
        "displacement": "m",
        "stress": "Pa",
        "force": "N",
        "pressure": "Pa",
    }
    #: 本次求解采用的输入长度单位（便于前端核对与展示）
    length_unit: str = "m"

async def solve_impl(request: "SolverRequest"):
    """
    线弹性静力求解（实现，在后台工作线程中执行，见 jobs.py）。
    """
    try:
        length_unit = validate_length_unit(request.length_unit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    length_scale = length_scale_to_meter(length_unit)

    # Check for demo mode shortcut
    if request.geometry_filename == "default_cube.step" and len(request.boundary_conditions) > 0:
        # If it's the demo cube, we can try to return a pre-calculated result if available, 
        # or just proceed with normal solve. 
        # For "Instant Demo", we can actually just generate a synthetic result without running FEM if we wanted to cheat,
        # but running the actual FEM is better for authenticity.
        # However, to make it robust, we can ensure mesh exists.
        pass

    try:
        file_path = str(resolve_upload_path(request.geometry_filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")
    
    # 1. Load Material Properties
    material = next((m for m in MATERIALS_DB if m.id == request.material_id), None)
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")
    
    E = material.youngsModulus
    nu = material.poissonsRatio
    
    try:
        # 2. Load Mesh (Gmsh -> scikit-fem, no meshio dependency)
        msh_path = file_path + ".msh"
        
        # Check if MSH exists
        if not os.path.exists(msh_path):
            # If not found, generate it now (fallback)
            logger.info("网格文件不存在，正在现场生成：%s", msh_path)
            start_model("SolverModel")
            gmsh.merge(file_path)
            gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0) # Default size
            gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
            gmsh.model.mesh.generate(3)
            gmsh.write(msh_path)
        else:
            logger.info("使用已缓存的网格：%s", msh_path)

        # Load mesh into scikit-fem（同时取回「面 → 边界三角形」映射）
        mesh, face_triangles = load_tet_mesh_from_msh(msh_path)

        # 单位换算：几何坐标先换算成米再组装，于是材料 E（Pa）、载荷（N）与
        # 结果（m / Pa）全部落在 SI 上。mesh_size 与坐标同单位，无需单独换算。
        if length_scale != 1.0:
            mesh = MeshTet(np.ascontiguousarray(mesh.p * length_scale), mesh.t)
            logger.info(
                "长度单位 %s：坐标已换算为米（×%g）", length_unit, length_scale
            )

        logger.info(
            "网格已载入：%d 个单元 / %d 个节点；可精确定位的面 %d 个",
            mesh.t.shape[1], mesh.p.shape[1], len(face_triangles),
        )

        # 3. Define Element and Basis
        # Linear Tetrahedral Element (Vector H1)
        element_vec = ElementVectorH1(ElementTetP1())
        basis_vec = Basis(mesh, element_vec)

        # 4. Assemble Stiffness Matrix (Linear Elasticity)
        # Lame parameters
        lam, mu = lame_parameters(E, nu)
        
        # K = stiffness matrix
        K = asm(linear_elasticity(lam, mu), basis_vec)
        
        # 5. 施加边界条件
        #
        # 定位节点有两条路径：
        #   A. 精确路径：Gmsh 在 .msh 里保留了「几何面 → 边界三角形」的对应关系，
        #      直接取该面的节点，并按**归属面积**加权（见 nodal_tributary_areas）。
        #      这是施加真实面载荷（压力/分布力）的正确做法：节点力之和 = 载荷 × 面积。
        #   B. 退化路径：拿不到三角形时（例如网格由老版本生成），沿用几何搜索
        #      （点到平面距离 / 包围盒），并按节点数平均分配合力。
        f = np.zeros(basis_vec.N)
        #: DOF → 指定位移值。0 表示固定约束（齐次），非 0 表示强制位移（非齐次）。
        prescribed_dofs: Dict[int, float] = {}
        solver_warnings: List[str] = []

        supported_types = {"fixed", "displacement", "force", "pressure"}
        model_centroid = mesh.p.mean(axis=1)
        # 容差一律用**相对模型尺度**表示（坐标已换算成米，绝对容差在毫米模型上会失效）
        model_span = compute_model_span(mesh)

        for bc in request.boundary_conditions:
            target_face = next((face for face in request.faces if face.id == bc.entityIndex), None)
            triangles = (
                face_triangles.get(int(bc.entityIndex))
                if getattr(bc, "applicationType", "face") == "face"
                else None
            )

            if bc.type not in supported_types:
                message = (
                    f"边界条件「{bc.name}」的类型 '{bc.type}' 暂未被求解器支持，已忽略"
                    "（当前支持：固定约束 / 强制位移 / 力载荷 / 压力）"
                )
                logger.warning(message)
                solver_warnings.append(message)
                continue

            # ---- 定位作用对象（与传热求解器共用同一套规则，见 fe_utils） ----
            target_nodes_indices, nodal_areas, locate_error = resolve_target_nodes(
                bc, mesh, face_triangles, request.faces, length_scale, model_span
            )
            if locate_error:
                logger.warning(locate_error)
                solver_warnings.append(locate_error)
                continue

            if len(target_nodes_indices) == 0:
                message = f"边界条件「{bc.name}」没有选中任何节点，已忽略"
                logger.warning(message)
                solver_warnings.append(message)
                continue

            if len(target_nodes_indices) == 0:
                message = f"边界条件「{bc.name}」没有选中任何节点，已忽略"
                logger.warning(message)
                solver_warnings.append(message)
                continue

            # ---- 施加 ---------------------------------------------------------
            if bc.type == "fixed":
                for node_idx in target_nodes_indices:
                    for component in range(3):
                        prescribed_dofs[int(basis_vec.nodal_dofs[component][node_idx])] = 0.0

            elif bc.type == "displacement":
                # 逐分量控制：fixedX/fixedY/fixedZ 决定约束哪些方向，值取自 displacement。
                # 前端 DisplacementConstraint 的默认值是三个方向全固定、位移为 0，
                # 语义上等价于固定约束。
                values = _parse_displacement(bc)
                flags = (
                    getattr(bc, "fixedX", None),
                    getattr(bc, "fixedY", None),
                    getattr(bc, "fixedZ", None),
                )
                # 三个标志都缺省时，按"全部约束"处理（与前端默认一致）
                if all(flag is None for flag in flags):
                    flags = (True, True, True)

                applied_components = [
                    component for component in range(3) if flags[component]
                ]
                if not applied_components:
                    message = (
                        f"强制位移「{bc.name}」没有勾选任何约束方向，已忽略"
                    )
                    logger.warning(message)
                    solver_warnings.append(message)
                    continue

                for component in applied_components:
                    value = float(values[component])
                    for node_idx in target_nodes_indices:
                        dof = int(basis_vec.nodal_dofs[component][node_idx])
                        prescribed_dofs[dof] = value

                logger.debug(
                    "强制位移作用于 %s 的 %d 个节点，方向 %s，值 %s",
                    bc.entityIndex, len(target_nodes_indices),
                    applied_components, [values[c] for c in applied_components],
                )

            elif bc.type == "force":
                fx, fy, fz = _parse_force(bc.force)
                total = np.array([fx, fy, fz], dtype=np.float64)

                # 按归属面积分配：每个节点得到 traction * A_node，
                # 于是 Σf = traction * ΣA = 输入的总力，但分布是物理的。
                if nodal_areas is not None and nodal_areas.sum() > 0:
                    weights = nodal_areas
                else:
                    weights = np.full(len(target_nodes_indices), 1.0)

                weights = weights / weights.sum()
                for weight, node_idx in zip(weights, target_nodes_indices):
                    f[basis_vec.nodal_dofs[0][node_idx]] += total[0] * weight
                    f[basis_vec.nodal_dofs[1][node_idx]] += total[1] * weight
                    f[basis_vec.nodal_dofs[2][node_idx]] += total[2] * weight

            elif bc.type == "pressure":
                pressure = _parse_pressure(bc)
                normal = np.asarray(target_face.normal, dtype=np.float64) if (
                    target_face and target_face.normal
                ) else None

                if normal is None and triangles is not None:
                    normal = surface_normal_from_triangles(
                        mesh.p, triangles, model_centroid
                    )

                if normal is None:
                    message = (
                        f"压力边界条件「{bc.name}」无法确定面法向（可能是曲面），已忽略"
                    )
                    logger.warning(message)
                    solver_warnings.append(message)
                    continue

                if nodal_areas is None:
                    nodal_areas = np.full(len(target_nodes_indices), 1.0 / len(target_nodes_indices))

                # 前端的约定：正压力指向实体内部 ⇒ traction = -p * n_outward
                traction = -pressure * normal
                for area, node_idx in zip(nodal_areas, target_nodes_indices):
                    nodal_force = traction * area
                    f[basis_vec.nodal_dofs[0][node_idx]] += nodal_force[0]
                    f[basis_vec.nodal_dofs[1][node_idx]] += nodal_force[1]
                    f[basis_vec.nodal_dofs[2][node_idx]] += nodal_force[2]

                logger.debug(
                    "压力 %.6g 作用于面 %s（%d 个节点，总面积 %.6g）",
                    pressure, bc.entityIndex, len(target_nodes_indices),
                    float(nodal_areas.sum()),
                )

        # 6. Solve
        #
        # 指定位移（非齐次 Dirichlet）：condense 的 x 参数是**全长向量**，
        # 只有 x[D] 会被用到（见 skfem.utils.condense 实现：
        # bout = b[I] - A[I][:, D] @ x[D]），expand=True 会把 u_D 填回解向量。
        D = np.array(sorted(prescribed_dofs), dtype=np.int64)

        # Check if f is all zero and no fixed dofs (to prevent singular matrix if user messed up)
        if len(D) == 0:
             # Fallback: Fix 3 corners to prevent rigid body motion if no constraints
             # This is just to ensure solver doesn't crash, result will be meaningless rigid body
             logger.warning("未检测到任何固定约束，已自动约束 3 个远端点以避免矩阵奇异——结果无物理意义，请添加固定约束")
             solver_warnings.append("缺少固定约束：已自动添加临时约束，结果不可用于判断")
             # Find 3 nodes that are far apart
             # 0, max_x_idx, max_y_idx
             p = mesh.p
             n1 = 0
             n2 = np.argmax(p[0])
             n3 = np.argmax(p[1])
             
             fallback_nodes = [n1, n2, n3]
             fallback_dofs = []
             for n in fallback_nodes:
                 fallback_dofs.extend([basis_vec.nodal_dofs[0][n], basis_vec.nodal_dofs[1][n], basis_vec.nodal_dofs[2][n]])
             D = np.unique(fallback_dofs)

        prescribed_values = np.zeros(basis_vec.N, dtype=np.float64)
        for dof, value in prescribed_dofs.items():
            prescribed_values[dof] = value

        # Solve Linear System: K u = f
        u = solve(*condense(K, f, x=prescribed_values, D=D))

        # Calculate Reaction Forces at fixed constraints: R = K * u - f
        # R will be non-zero only at constrained DOFs
        R_full = K @ u - f
        
        reaction_forces = {}
        for dof in D:
            # Determine node index and component (x=0, y=1, z=2)
            # nodal_dofs is shape (3, N_nodes)
            node_indices = np.where(basis_vec.nodal_dofs == dof)
            if len(node_indices[0]) > 0:
                comp = node_indices[0][0] # 0, 1, or 2
                node_idx = node_indices[1][0]
                
                node_str = str(node_idx)
                if node_str not in reaction_forces:
                    reaction_forces[node_str] = [0.0, 0.0, 0.0]
                
                reaction_forces[node_str][comp] = float(R_full[dof])

        # 7. Post-Processing: Calculate Von Mises Stress
        
        # Linear-elastic stress-strain relation sigma = 2*mu*e + lam*tr(e)*I
        C = linear_stress(lam, mu)

        # Evaluate the stress at the quadrature points of the vector basis and
        # L2-project it onto the P1 nodal basis. (In scikit-fem 12 a Functional
        # can no longer be handed to Basis.project, so the values are evaluated
        # directly; both bases share ElementTetP1 and therefore the same
        # quadrature points.)
        #
        # 注意 s_dev 的写法：``eye(w, n)`` 已经是「把 w 放到对角线上」，
        # 因此偏应力只需 ``s - (1/3)*eye(trace(s), 3)``。
        # 曾经写成 ``trace(s) * eye(trace(s), 3)``——等于把迹又乘了一遍，
        # 对角项变成 tr²，导致 Von Mises 应力被放大了好几个数量级
        # （静水压力越大错得越多）。参见 tests 里的解析解回归测试。
        u_interp = basis_vec.interpolate(u)
        strain_qp = sym_grad(u_interp)
        stress_qp = C(strain_qp)
        # Deviatoric stress: s_dev = s - 1/3 * tr(s) * I
        stress_dev_qp = stress_qp - (1.0 / 3.0) * eye(trace(stress_qp), 3)
        # Von Mises: sqrt(3/2 * s_dev : s_dev), shape (n_qp, n_elements)
        von_mises_qp = np.sqrt(1.5 * ddot(stress_dev_qp, stress_dev_qp))

        basis_scalar = Basis(mesh, ElementTetP1())
        stress_vals = basis_scalar.project(von_mises_qp)
        
        # Extract displacements
        u_x = u[basis_vec.nodal_dofs[0]].flatten()
        u_y = u[basis_vec.nodal_dofs[1]].flatten()
        u_z = u[basis_vec.nodal_dofs[2]].flatten()
        
        displacements = []
        max_disp = 0.0
        for i in range(len(u_x)):
            d = [float(u_x[i]), float(u_y[i]), float(u_z[i])]
            displacements.append(d)
            disp_mag = np.sqrt(d[0]**2 + d[1]**2 + d[2]**2)
            if disp_mag > max_disp:
                max_disp = disp_mag
                
        # Use real calculated stress
        stresses = [float(s) for s in stress_vals]
        # Replace NaN with 0 (can happen if mesh is bad)
        stresses = [0.0 if np.isnan(s) else s for s in stresses]
        max_stress = max(stresses) if stresses else 0.0

        if solver_warnings:
            logger.warning("本次求解有 %d 条警告，请检查边界条件", len(solver_warnings))

        return SolverResult(
            status="solved",
            message="Simulation completed successfully.",
            max_displacement=max_disp,
            max_stress=max_stress,
            displacements=displacements,
            stresses=stresses,
            reaction_forces=reaction_forces,
            warnings=solver_warnings,
            length_unit=length_unit,
        )

    except HTTPException:
        raise
    except Exception as e:
        # 不再 finalize gmsh：会话在进程内复用（信号处理只能在主线程设置，
        # 而且这里可能运行在工作线程里）。失败时留下日志即可。
        logger.exception("求解失败")
        raise HTTPException(status_code=500, detail=f"Solver failed: {str(e)}")

@router.post("/solve", response_model=SolverResult)
async def solve_simulation(request: SolverRequest):
    """
    线弹性静力求解（同步接口）。

    实现排在单线程工作器里执行，原因见 geometry.py 末尾的说明：
    gmsh 非线程安全，且求解是 CPU 密集操作。
    大模型建议改用 POST /api/jobs/solve（提交后轮询，避免请求超时）。
    """
    return await run_in_worker(solve_impl, request=request)
