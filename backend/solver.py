from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import numpy as np
from skfem import *
from skfem.helpers import dot, grad, trace, sym_grad, eye, identity, ddot
from skfem.models.elasticity import linear_elasticity, linear_stress, lame_parameters
import os
import gmsh

# Import local modules
from config import resolve_upload_path
from logging_config import get_logger
from geometry import FaceInfo
from materials import MATERIALS_DB
from constraints import BoundaryCondition

router = APIRouter()
logger = get_logger(__name__)


def _parse_force(force) -> tuple:
    """把前端传来的力（dict / Vector3 / list）统一成 ``(fx, fy, fz)``。"""
    if isinstance(force, dict):
        return (
            float(force.get('x', 0.0) or 0.0),
            float(force.get('y', 0.0) or 0.0),
            float(force.get('z', 0.0) or 0.0),
        )
    if hasattr(force, 'x'):
        return (float(force.x), float(force.y), float(force.z))
    if isinstance(force, (list, tuple)) and len(force) == 3:
        return (float(force[0]), float(force[1]), float(force[2]))
    return (0.0, 0.0, 0.0)


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
    """取强制位移值，统一成 ``(ux, uy, uz)``（dict / Vector3 / list 均可）。"""
    return _parse_force(getattr(bc, "displacement", None))


def load_tet_mesh_from_msh(msh_path: str):
    """
    Build a scikit-fem tetrahedral mesh directly with Gmsh.

    ``Mesh.load`` would delegate to meshio, which is not part of the backend
    dependencies. Gmsh is already required for meshing, so we read the mesh back
    with it instead of adding another dependency.

    Returns
    -------
    mesh
        The ``MeshTet`` volume mesh.
    face_triangles
        ``{entity_tag: ndarray(n_triangles, 3)}`` — 每个几何面所包含的**边界三角形**
        及其在 scikit-fem 网格中的节点索引。有了它就能精确定位一个面上的节点，
    不必再用「点到平面距离」去猜；同时可以算出每个节点的**归属面积**，
    从而施加真实的面载荷（traction）而不是把合力平均分给节点。
    """
    if not gmsh.isInitialized():
        gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()

    face_triangles: Dict[int, np.ndarray] = {}

    try:
        gmsh.open(msh_path)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        nodes = np.asarray(node_coords, dtype=np.float64).reshape(-1, 3)
        node_tags = np.asarray(node_tags, dtype=np.int64)

        # Node tags are not guaranteed to start at 0, so map them explicitly.
        tag_to_index = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
        tag_to_index[node_tags] = np.arange(len(node_tags), dtype=np.int64)

        element_types, _, element_nodes = gmsh.model.mesh.getElements(dim=3)

        cells = []
        for etype, enodes in zip(element_types, element_nodes):
            if etype != 4:  # keep 4-node tetrahedra only
                continue
            conn = np.asarray(enodes, dtype=np.int64).reshape(-1, 4)
            cells.append(tag_to_index[conn])

        # 逐面取出边界三角形（MSH 4.1 会保留几何实体标签，因此 tag 与前端
        # 从元数据接口拿到的 face.id 一致）
        for _, face_tag in gmsh.model.getEntities(dim=2):
            try:
                ftypes, _, fnodes = gmsh.model.mesh.getElements(dim=2, tag=face_tag)
            except Exception:
                continue
            triangles = []
            for ftype, fconn in zip(ftypes, fnodes):
                if ftype != 2:  # 只要 3 节点三角形
                    continue
                triangles.append(np.asarray(fconn, dtype=np.int64).reshape(-1, 3))
            if triangles:
                face_triangles[int(face_tag)] = tag_to_index[np.vstack(triangles)]
    finally:
        if gmsh.isInitialized():
            gmsh.finalize()

    if not cells:
        raise ValueError("No 4-node tetrahedral elements found in the mesh file.")

    t = np.vstack(cells).T.astype(np.int64)  # shape (4, n_elements)
    points = nodes.T  # shape (3, n_nodes)

    # scikit-fem expects a positive Jacobian per element; flip inverted ones.
    # Note: NumPy >= 2.0 evaluates np.cross along the LAST axis, so the edge
    # vectors are transposed to (n_elements, 3) before taking the cross product.
    v0, v1, v2, v3 = (points[:, t[i]] for i in range(4))
    e1 = (v1 - v0).T
    e2 = (v2 - v0).T
    e3 = (v3 - v0).T
    det = np.einsum('ij,ij->i', np.cross(e1, e2), e3)
    flip = det < 0
    if np.any(flip):
        t[[1, 2], flip] = t[[2, 1], flip]

    return MeshTet(points, t), face_triangles


def nodal_tributary_areas(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """
    计算一组三角形上每个节点的**归属面积**（lumped / tributary area）。

    对线性三角形，每个节点分得所在三角形面积的 1/3。所有节点归属面积之和
    精确等于这组三角形的总面积——这正是施加均匀面载荷时需要的权重：
    ``f_node = traction * A_node`` 的和就等于 ``traction * A_total``。
    """
    areas = np.zeros(points.shape[1], dtype=np.float64)
    if triangles.size == 0:
        return areas

    tri_pts = points[:, triangles]          # (3, n_tri, 3)
    p0, p1, p2 = tri_pts[:, :, 0], tri_pts[:, :, 1], tri_pts[:, :, 2]
    cross = np.cross((p1 - p0).T, (p2 - p0).T)
    tri_area = 0.5 * np.linalg.norm(cross, axis=1)

    share = tri_area / 3.0
    for corner in range(3):
        np.add.at(areas, triangles[:, corner], share)

    return areas


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

@router.post("/solve", response_model=SolverResult)
async def solve_simulation(request: SolverRequest):
    """
    Perform Linear Static Structural Analysis using scikit-fem.
    """
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
            if not gmsh.isInitialized():
                gmsh.initialize()
            gmsh.clear()
            gmsh.model.add("SolverModel")
            gmsh.merge(file_path)
            gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0) # Default size
            gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
            gmsh.model.mesh.generate(3)
            gmsh.write(msh_path)
            gmsh.finalize()
        else:
            logger.info("使用已缓存的网格：%s", msh_path)

        # Load mesh into scikit-fem（同时取回「面 → 边界三角形」映射）
        mesh, face_triangles = load_tet_mesh_from_msh(msh_path)
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

        for bc in request.boundary_conditions:
            x, y, z = mesh.p

            target_face = next((face for face in request.faces if face.id == bc.entityIndex), None)
            triangles = (
                face_triangles.get(int(bc.entityIndex))
                if getattr(bc, "applicationType", "face") == "face"
                else None
            )

            target_nodes_indices = np.array([], dtype=np.int64)
            nodal_areas = None

            if bc.type not in supported_types:
                message = (
                    f"边界条件「{bc.name}」的类型 '{bc.type}' 暂未被求解器支持，已忽略"
                    "（当前支持：固定约束 / 强制位移 / 力载荷 / 压力）"
                )
                logger.warning(message)
                solver_warnings.append(message)
                continue

            # ---- 定位作用对象 -------------------------------------------------
            if getattr(bc, "applicationType", "face") == "vertex":
                if 0 <= bc.entityIndex < mesh.p.shape[1]:
                    target_nodes_indices = np.array([bc.entityIndex], dtype=np.int64)
                else:
                    message = f"顶点索引 {bc.entityIndex} 超出范围，已忽略该边界条件"
                    logger.warning(message)
                    solver_warnings.append(message)
                    continue

            elif triangles is not None and triangles.size > 0:
                # 精确路径
                target_nodes_indices = np.unique(triangles)
                nodal_areas = nodal_tributary_areas(mesh.p, triangles)[target_nodes_indices]
                logger.debug(
                    "面 %s：由边界三角形定位到 %d 个节点，总面积 %.6g",
                    bc.entityIndex, len(target_nodes_indices), float(nodal_areas.sum()),
                )

            else:
                # 退化路径：几何搜索（保留旧行为，供 STL / 旧网格使用）
                if target_face and target_face.normal:
                    nx, ny, nz = target_face.normal
                    cx, cy, cz = target_face.center
                    dist_to_plane = np.abs(
                        (x - cx) * nx + (y - cy) * ny + (z - cz) * nz
                    )
                    target_nodes_indices = np.where(dist_to_plane < 1e-2)[0]

                if len(target_nodes_indices) == 0:
                    bbox_dims = [x.max() - x.min(), y.max() - y.min(), z.max() - z.min()]
                    max_dim = max(bbox_dims) if bbox_dims else 1.0
                    tol = max(max_dim * 0.1, 1e-3)

                    idx = bc.entityIndex % 6
                    if idx == 0: mask = x < x.min() + tol
                    elif idx == 1: mask = x > x.max() - tol
                    elif idx == 2: mask = y < y.min() + tol
                    elif idx == 3: mask = y > y.max() - tol
                    elif idx == 4: mask = z < z.min() + tol
                    else: mask = z > z.max() - tol

                    target_nodes_indices = np.where(mask)[0]
                    logger.debug(
                        "面 %s：精确映射不可用，回退到包围盒启发式，命中 %d 个节点",
                        bc.entityIndex, len(target_nodes_indices),
                    )

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
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Ensure gmsh is finalized if error occurs during mesh generation/loading
        if gmsh.isInitialized():
            gmsh.finalize()
        raise HTTPException(status_code=500, detail=f"Solver failed: {str(e)}")
