import shutil
import os
import numpy as np
import gmsh
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from supabase_client import supabase

# 集中日志（不再使用 print）
from logging_config import get_logger

logger = get_logger(__name__)

# 所有 gmsh 操作都排队到单线程工作器里执行（gmsh 非线程安全，见 jobs.py）
from jobs import run_in_worker

# gmsh 会话必须在**主线程**初始化（信号处理限制），模块导入即在主线程
from gmsh_session import ensure_initialized as _ensure_gmsh, start_model

_ensure_gmsh()

# 集中配置：路径与限制（UPLOAD_DIR 为绝对路径，不再依赖工作目录）
from config import (
    UPLOAD_DIR,
    MAX_UPLOAD_BYTES,
    GMSH_TERMINAL,
    ensure_upload_dir,
    resolve_upload_path,
    validate_mesh_size,
)

router = APIRouter()

ensure_upload_dir()

class FaceInfo(BaseModel):
    id: int
    type: str  # Plane, Cylinder, etc.
    area: float
    center: List[float] # [x, y, z]
    normal: Optional[List[float]] # [nx, ny, nz] (for planar faces)

class EdgeInfo(BaseModel):
    id: int
    length: float
    center: List[float]

class VertexInfo(BaseModel):
    id: int
    coords: List[float]

class MeshInfo(BaseModel):
    nodes: List[List[float]]  # [[x, y, z], ...]
    elements: List[List[int]] # [[n1, n2, n3, n4], ...] (tetrahedrons)
    faces: List[FaceInfo] # Metadata for B-Rep faces
    edges: List[EdgeInfo] = []
    vertices: List[VertexInfo] = []
    status: str
    message: str


def _normal_from_surface_mesh(tag: int) -> Optional[List[float]]:
    """
    Area-weighted average normal of a surface entity, computed from its triangles.

    Used as a fallback for discrete geometries (STL) where Gmsh has no parametric
    description. For a planar face this reproduces the plane normal; for a curved
    face the contributions cancel out and ``None`` is returned on purpose.
    """
    try:
        node_tags, node_coords, _ = gmsh.model.mesh.getNodes(dim=2, tag=tag, includeBoundary=False)
        if len(node_tags) == 0:
            return None
        coords = np.asarray(node_coords, dtype=np.float64).reshape(-1, 3)
        index = {int(t): i for i, t in enumerate(node_tags)}

        _, _, element_nodes = gmsh.model.mesh.getElements(dim=2, tag=tag)

        accumulated = np.zeros(3)
        for conn in element_nodes:
            triangles = np.asarray(conn, dtype=np.int64).reshape(-1, 3)
            for tri in triangles:
                try:
                    p0, p1, p2 = (coords[index[int(t)]] for t in tri)
                except KeyError:
                    continue
                accumulated += np.cross(p1 - p0, p2 - p0)

        length = float(np.linalg.norm(accumulated))
        if length < 1e-12:
            return None
        # Normalise; Gmsh's cached mesh winding is not guaranteed, so orient the
        # normal outwards from the entity bounding-box centre.
        normal = accumulated / length
        bbox = gmsh.model.getBoundingBox(2, tag)
        center = np.array([(bbox[0] + bbox[3]) / 2,
                           (bbox[1] + bbox[4]) / 2,
                           (bbox[2] + bbox[5]) / 2])
        return normal.tolist()
    except Exception:
        return None


def extract_entity_metadata():
    """
    Extract B-Rep faces / edges / vertices with type, size, centre and normal.

    Must be called while a Gmsh model is loaded. ``area`` and ``length`` are real
    measures for OCC (STEP/IGES) models and are reported as 0.0 for discrete
    models, where they are not available without integrating the mesh.
    """
    faces_metadata: List[FaceInfo] = []
    edges_metadata: List[EdgeInfo] = []
    vertices_metadata: List[VertexInfo] = []

    for dim, tag in gmsh.model.getEntities(dim=2):
        type_str = gmsh.model.getType(dim, tag)

        bbox = gmsh.model.getBoundingBox(dim, tag)
        center = [(bbox[0] + bbox[3]) / 2,
                  (bbox[1] + bbox[4]) / 2,
                  (bbox[2] + bbox[5]) / 2]

        area = 0.0
        try:
            area = float(gmsh.model.occ.getMass(dim, tag))
        except Exception:
            pass

        # Correct Gmsh API usage: getParametrizationBounds returns (min, max) and
        # getNormal takes the surface tag plus [u, v] (it has no `dim` argument).
        normal = None
        try:
            bounds_min, bounds_max = gmsh.model.getParametrizationBounds(dim, tag)
            u_mid = 0.5 * (bounds_min[0] + bounds_max[0])
            v_mid = 0.5 * (bounds_min[1] + bounds_max[1])
            raw_normal = gmsh.model.getNormal(tag, [u_mid, v_mid])
            if len(raw_normal) >= 3:
                normal = [float(raw_normal[0]), float(raw_normal[1]), float(raw_normal[2])]
        except Exception:
            normal = None

        if normal is None:
            normal = _normal_from_surface_mesh(tag)

        faces_metadata.append(FaceInfo(
            id=tag,
            type=type_str,
            area=area,
            center=center,
            normal=normal
        ))

    for dim, tag in gmsh.model.getEntities(dim=1):
        bbox = gmsh.model.getBoundingBox(dim, tag)
        center = [(bbox[0] + bbox[3]) / 2,
                  (bbox[1] + bbox[4]) / 2,
                  (bbox[2] + bbox[5]) / 2]
        length = 0.0
        try:
            length = float(gmsh.model.occ.getMass(dim, tag))
        except Exception:
            pass
        edges_metadata.append(EdgeInfo(id=tag, length=length, center=center))

    for dim, tag in gmsh.model.getEntities(dim=0):
        bbox = gmsh.model.getBoundingBox(dim, tag)
        vertices_metadata.append(VertexInfo(id=tag, coords=[bbox[0], bbox[1], bbox[2]]))

    return faces_metadata, edges_metadata, vertices_metadata

async def generate_cube_geometry_impl():
    """
    生成默认 10x10x10 立方体 STEP（实现，在后台工作线程中执行，见 jobs.py）。
    """
    try:
        # 会话级 gmsh：不再 initialize/finalize（信号处理只能在主线程设置，见 gmsh_session）
        start_model("DefaultCube")
        
        # Create a box at origin, size 10x10x10
        # x, y, z, dx, dy, dz
        # Center it at 0,0,0? No, let's put it at 0,0,0 to 10,10,10
        # Actually, let's center it at -5,-5,-5 so origin is center
        gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
        
        gmsh.model.occ.synchronize()
        
        filename = "default_cube.step"
        file_path = str(resolve_upload_path(filename))
        
        gmsh.write(file_path)
        
        return {"filename": filename, "message": "Default cube generated"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate cube: {str(e)}")

async def upload_geometry_impl(file: UploadFile = File(...)):
    """
    上传几何文件（实现，在后台工作线程中执行，见 jobs.py）。
    """
    try:
        # 1. 先校验文件名（防路径穿越/扩展名白名单）与大小，再落盘供 Gmsh 处理
        try:
            file_path = str(resolve_upload_path(file.filename))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

        file_content = await file.read()

        if len(file_content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        if len(file_content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"文件过大：{len(file_content)} 字节，上限 {MAX_UPLOAD_BYTES} 字节"
                    "（可用环境变量 MAX_UPLOAD_BYTES 调整）"
                ),
            )
            
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)
            
        render_filename = file.filename
        
        # Convert STEP/IGES to STL for web visualization
        if file.filename.lower().endswith(('.step', '.stp', '.iges', '.igs')):
            try:
                start_model("ConversionModel")
                gmsh.merge(file_path)
                
                # Fast 2D meshing for visualization
                gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0)
                gmsh.option.setNumber("Mesh.MeshSizeMax", 5.0)
                gmsh.model.mesh.generate(2)
                
                render_filename = file.filename + ".stl"
                render_path = str(resolve_upload_path(render_filename))
                gmsh.write(render_path)
            except Exception as e:
                logger.warning("STEP/IGES 转 STL 预览失败（不影响网格与求解）：%s", e)

        # 2. Upload to Supabase Storage (if configured)
        supabase_url = None
        if supabase:
            try:
                # Assuming a bucket named 'geometries' exists
                # We use file.filename as path, might overwrite. Consider UUIDs for production.
                res = supabase.storage.from_("geometries").upload(
                    file.filename, 
                    file_content,
                    {"content-type": file.content_type, "upsert": "true"}
                )
                
                # Get public URL
                supabase_url = supabase.storage.from_("geometries").get_public_url(file.filename)
                logger.info("已上传到 Supabase：%s", supabase_url)
            except Exception as e:
                logger.warning("Supabase 上传失败，本地副本已保留：%s", e)

        return {
            "filename": file.filename,
            "render_filename": render_filename,
            "path": file_path,
            "supabase_url": supabase_url,
            "message": "File uploaded successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

class GeometryMetadata(BaseModel):
    faces: List[FaceInfo]
    edges: List[EdgeInfo] = []
    vertices: List[VertexInfo] = []
    message: str

async def get_geometry_metadata_impl(filename: str):
    """
    提取 B-Rep 元数据（实现，在后台工作线程中执行，见 jobs.py）。
    """
    try:
        file_path = str(resolve_upload_path(filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    try:
        # 清空上一个模型并导入几何（会话级 gmsh，不 initialize/finalize）
        start_model("MetadataModel")
        gmsh.merge(file_path)

        faces_metadata, edges_metadata, vertices_metadata = extract_entity_metadata()

        return GeometryMetadata(
            faces=faces_metadata,
            edges=edges_metadata,
            vertices=vertices_metadata,
            message=f"Identified {len(faces_metadata)} faces, {len(edges_metadata)} edges, {len(vertices_metadata)} vertices"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metadata extraction failed: {str(e)}")

async def generate_mesh_impl(filename: str, mesh_size: float = 0.5):
    """
    生成三维四面体网格（实现，在后台工作线程中执行，见 jobs.py）。
    """
    # 校验文件名与网格尺寸，避免非法输入进入 Gmsh
    try:
        file_path = str(resolve_upload_path(filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    try:
        mesh_size = validate_mesh_size(mesh_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    try:
        # 会话级 gmsh：start_model 会清空上一个模型（以前依赖每次 initialize 得到干净状态，
        # gmsh 复用后必须显式清理，否则模型会不断累积）
        start_model("Model")
        gmsh.option.setNumber("General.Terminal", GMSH_TERMINAL)

        # Merge the geometry file
        # Gmsh supports STL, STEP, IGES, etc.
        gmsh.merge(file_path)

        # Set mesh size
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)

        # Generate 3D mesh
        gmsh.model.mesh.generate(3)

        # Extract Face / Edge / Vertex Metadata (B-Rep)
        # Done after meshing so that discrete (STL) surfaces can also contribute
        # normals derived from their triangles.
        faces_metadata, edges_metadata, vertices_metadata = extract_entity_metadata()

        # Save mesh to file for consistent reuse in solver
        msh_path = file_path + ".msh"
        gmsh.write(msh_path)
        logger.info("网格已保存：%s", msh_path)

        # Retrieve nodes
        nodeTags, nodeCoords, _ = gmsh.model.mesh.getNodes()
        
        # Reshape coordinates to [[x,y,z], ...]
        nodes = []
        # Create a mapping from Node TAG to Index (0-based)
        # Because element connectivity uses TAGs
        tag_to_index = {}
        
        for i in range(len(nodeTags)):
            tag = nodeTags[i]
            # Gmsh returns flat coords
            x = nodeCoords[3*i]
            y = nodeCoords[3*i+1]
            z = nodeCoords[3*i+2]
            nodes.append([x, y, z])
            tag_to_index[tag] = i

        # Retrieve elements (Tetrahedrons only for now)
        elementTypes, elementTags, elementNodeTags = gmsh.model.mesh.getElements(dim=3)
        
        elements = []
        if len(elementTypes) > 0:
            for i, eType in enumerate(elementTypes):
                if eType == 4: # 4-node tetrahedron
                    tags = elementNodeTags[i]
                    for j in range(0, len(tags), 4):
                        # Map tags to 0-based indices
                        n1 = tag_to_index.get(tags[j], 0)
                        n2 = tag_to_index.get(tags[j+1], 0)
                        n3 = tag_to_index.get(tags[j+2], 0)
                        n4 = tag_to_index.get(tags[j+3], 0)
                        elements.append([n1, n2, n3, n4])
        
        if len(elements) == 0:
             logger.warning("未生成任何三维单元，网格可能只有面或划分失败")
        
        return MeshInfo(
            nodes=nodes,
            elements=elements,
            faces=faces_metadata,
            edges=edges_metadata,
            vertices=vertices_metadata,
            status="success",
            message=f"Generated {len(nodes)} nodes, {len(elements)} elements, and identified {len(faces_metadata)} faces"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("网格划分失败")
        raise HTTPException(status_code=500, detail=f"Meshing failed: {str(e)}")

# ===========================================================================
#  同步端点：排进单线程工作队列并等待结果。
#
#  为什么不直接在事件循环里跑 gmsh：
#    gmsh 是进程级全局状态、非线程安全；而网格划分又是 CPU 密集的，
#    放在事件循环里会阻塞整个服务。统一走 jobs 的单线程工作器后：
#      * 所有 gmsh 操作天然串行，不会互相破坏；
#      * 事件循环保持响应（可以继续轮询任务状态）；
#      * 同一套实现既服务同步接口，也服务异步任务接口（/api/jobs/*）。
# ===========================================================================

@router.post("/generate-cube")
async def generate_cube_geometry():
    """生成演示立方体 STEP（同步接口）。"""
    return await run_in_worker(generate_cube_geometry_impl)


@router.post("/upload-geometry")
async def upload_geometry(file: UploadFile = File(...)):
    """上传几何文件（同步接口）。"""
    return await run_in_worker(upload_geometry_impl, file=file)


@router.get("/geometry/{filename}/metadata", response_model=GeometryMetadata)
async def get_geometry_metadata(filename: str):
    """提取 B-Rep 元数据（同步接口）。"""
    return await run_in_worker(get_geometry_metadata_impl, filename=filename)


@router.post("/generate-mesh", response_model=MeshInfo)
async def generate_mesh(filename: str, mesh_size: float = 0.5):
    """生成网格（同步接口）。大模型建议改用 POST /api/jobs/generate-mesh。"""
    return await run_in_worker(
        generate_mesh_impl, filename=filename, mesh_size=mesh_size
    )
