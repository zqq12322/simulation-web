"""
有限元公共基础设施：网格读取 + 几何拾取。

为什么单独成模块
----------------
结构求解器（`solver.py`）与传热求解器（`thermal.py`）都要回答同一个问题：
**「这个边界条件作用在哪些节点上？」** 这类规则必须只有一份实现——本项目
已经因为"两处各写一套"吃过亏（PowerShell 与验证逻辑重复、面载荷精度被两处
描述成不同结论）。所以统一抽到这里：谁要用谁 import，改一次两边都生效。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import gmsh
import numpy as np
from skfem import MeshTet

from gmsh_session import open_model_file
from logging_config import get_logger

logger = get_logger(__name__)


def load_tet_mesh_from_msh(msh_path: str):
    """
    用 Gmsh 把 `.msh` 读成 scikit-fem 的四面体网格。

    ``Mesh.load`` 会走 meshio，而它不在后端依赖里；Gmsh 本来就必须有，
    所以直接用它读回来，不额外引入依赖。

    Returns
    -------
    mesh
        ``MeshTet`` 体网格。
    face_triangles
        ``{entity_tag: ndarray(n_triangles, 3)}`` —— 每个几何面包含的**边界三角形**
        及其在网格中的节点索引。有了它就能精确定位面上的节点（不必再用
        「点到平面距离」去猜），也能算出节点**归属面积**以施加真实面载荷。
    """
    # 会话级 gmsh：只 open（内部等价 clear + merge），不 initialize/finalize
    # —— 信号处理只能在主线程设置，见 gmsh_session.py
    open_model_file(msh_path)
    gmsh.option.setNumber("General.Terminal", 0)

    face_triangles: Dict[int, np.ndarray] = {}

    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    nodes = np.asarray(node_coords, dtype=np.float64).reshape(-1, 3)
    node_tags = np.asarray(node_tags, dtype=np.int64)

    # 节点 tag 不保证从 0 开始，必须显式映射
    tag_to_index = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
    tag_to_index[node_tags] = np.arange(len(node_tags), dtype=np.int64)

    element_types, _, element_nodes = gmsh.model.mesh.getElements(dim=3)

    cells = []
    for etype, enodes in zip(element_types, element_nodes):
        if etype != 4:  # 只要 4 节点四面体
            continue
        conn = np.asarray(enodes, dtype=np.int64).reshape(-1, 4)
        cells.append(tag_to_index[conn])

    # 逐面取出边界三角形（MSH 4.1 保留几何实体标签，故 tag 与元数据接口的 face.id 一致）
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

    if not cells:
        raise ValueError("No 4-node tetrahedral elements found in the mesh file.")

    t = np.vstack(cells).T.astype(np.int64)  # shape (4, n_elements)
    points = nodes.T  # shape (3, n_nodes)

    # scikit-fem 要求单元雅可比为正，负的就翻转节点顺序。
    # 注意 NumPy >= 2.0 的 np.cross 沿**末轴**计算，因此先转置成 (n_elements, 3)。
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
    一组三角形上每个节点的**归属面积**（lumped / tributary area）。

    对线性三角形，每个节点分得所在三角形面积的 1/3；所有节点归属面积之和
    精确等于这组三角形的总面积。这既是施加均匀面载荷时的正确权重
    （``f_node = traction * A_node`` 之和 = ``traction * A_total``），
    也已用 ``FacetBasis`` 精确积分对照验证过（相对差异 3.7e-16）。
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


def compute_model_span(mesh) -> float:
    """
    模型的最大尺度。

    容差一律用**相对尺度**表示：坐标已经被换算成米，写死的绝对容差
    （最初的 1e-2）在毫米模型上会大到把整个模型都选进来。
    """
    return float(
        max(
            mesh.p[0].max() - mesh.p[0].min(),
            mesh.p[1].max() - mesh.p[1].min(),
            mesh.p[2].max() - mesh.p[2].min(),
            1e-30,
        )
    )


def resolve_target_nodes(
    bc,
    mesh,
    face_triangles: Dict[int, np.ndarray],
    faces: List,
    length_scale: float,
    span: float,
) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[str]]:
    """
    定位一个边界条件作用的节点集合（结构与传热共用）。

    定位顺序：
      1. **精确路径**：`.msh` 里的「几何面 → 边界三角形」；
      2. 退化路径：点到平面距离（用面元数据的法向）；
      3. 再退化：包围盒 6 个面按 ``entityIndex % 6`` 猜（只为兼容老的 STL/网格）。

    Returns
    -------
    (node_indices, nodal_areas, error)
        ``node_indices`` 可能为空数组；
        ``nodal_areas`` 只在精确路径下给出（退化路径为 ``None``，调用方按节点数均分）；
        ``error`` 非空表示无法定位，调用方应记 warning 并跳过。
    """
    indices = np.array([], dtype=np.int64)

    if getattr(bc, "applicationType", "face") == "vertex":
        if 0 <= bc.entityIndex < mesh.p.shape[1]:
            return np.array([bc.entityIndex], dtype=np.int64), None, None
        return indices, None, f"顶点索引 {bc.entityIndex} 超出范围，已忽略该边界条件"

    triangles = face_triangles.get(int(bc.entityIndex)) if face_triangles else None
    if triangles is not None and triangles.size > 0:
        indices = np.unique(triangles)
        areas = nodal_tributary_areas(mesh.p, triangles)[indices]
        logger.debug(
            "面 %s：由边界三角形精确定位到 %d 个节点（总面积 %.6g）",
            bc.entityIndex, len(indices), float(areas.sum()),
        )
        return indices, areas, None

    x, y, z = mesh.p
    target_face = next((face for face in faces if face.id == bc.entityIndex), None)

    if target_face is not None and target_face.normal:
        nx, ny, nz = target_face.normal
        # 面元数据来自原几何（输入单位），要与已换算成米的网格坐标对齐
        cx, cy, cz = (c * length_scale for c in target_face.center)
        dist_to_plane = np.abs((x - cx) * nx + (y - cy) * ny + (z - cz) * nz)
        indices = np.where(dist_to_plane < span * 1e-3)[0]

    if len(indices) == 0:
        tol = span * 0.1
        idx = bc.entityIndex % 6
        if idx == 0: mask = x < x.min() + tol
        elif idx == 1: mask = x > x.max() - tol
        elif idx == 2: mask = y < y.min() + tol
        elif idx == 3: mask = y > y.max() - tol
        elif idx == 4: mask = z < z.min() + tol
        else: mask = z > z.max() - tol
        indices = np.where(mask)[0]
        logger.debug(
            "面 %s：精确映射不可用，回退到包围盒启发式，命中 %d 个节点",
            bc.entityIndex, len(indices),
        )

    return indices, None, None
