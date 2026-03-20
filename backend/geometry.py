import shutil
import os
import gmsh
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from supabase_client import supabase

router = APIRouter()

# Directory to store uploaded geometry files locally (as a fallback or cache)
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

class FaceInfo(BaseModel):
    id: int
    type: str  # Plane, Cylinder, etc.
    area: float
    center: List[float] # [x, y, z]
    normal: Optional[List[float]] # [nx, ny, nz] (for planar faces)

class MeshInfo(BaseModel):
    nodes: List[List[float]]  # [[x, y, z], ...]
    elements: List[List[int]] # [[n1, n2, n3, n4], ...] (tetrahedrons)
    faces: List[FaceInfo] # Metadata for B-Rep faces
    status: str
    message: str

@router.post("/generate-cube")
async def generate_cube_geometry():
    """
    Generate a default 10x10x10 cube STEP file for demonstration.
    """
    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
        
        gmsh.clear()
        gmsh.model.add("DefaultCube")
        
        # Create a box at origin, size 10x10x10
        # x, y, z, dx, dy, dz
        # Center it at 0,0,0? No, let's put it at 0,0,0 to 10,10,10
        # Actually, let's center it at -5,-5,-5 so origin is center
        gmsh.model.occ.addBox(-5, -5, -5, 10, 10, 10)
        
        gmsh.model.occ.synchronize()
        
        filename = "default_cube.step"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        gmsh.write(file_path)
        
        gmsh.finalize()
        
        return {"filename": filename, "message": "Default cube generated"}
        
    except Exception as e:
        if gmsh.isInitialized():
            gmsh.finalize()
        raise HTTPException(status_code=500, detail=f"Failed to generate cube: {str(e)}")

@router.post("/upload-geometry")
async def upload_geometry(file: UploadFile = File(...)):
    """
    Upload a geometry file (STL, STEP, etc.) to the server and Supabase Storage.
    """
    try:
        # 1. Save locally for Gmsh processing
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        file_content = await file.read()
        
        if len(file_content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)
            
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
                print(f"Successfully uploaded to Supabase: {supabase_url}")
            except Exception as e:
                print(f"Supabase upload failed, but local copy succeeded: {e}")

        return {
            "filename": file.filename,
            "path": file_path,
            "supabase_url": supabase_url,
            "message": "File uploaded successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

class GeometryMetadata(BaseModel):
    faces: List[FaceInfo]
    message: str

@router.get("/geometry/{filename}/metadata", response_model=GeometryMetadata)
async def get_geometry_metadata(filename: str):
    """
    Extract geometry metadata (faces, etc.) without generating a full mesh.
    Useful for visualization and boundary condition setup.
    """
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
        
        # Clear previous models
        gmsh.clear()
        gmsh.model.add("MetadataModel")
        
        # Merge the geometry file
        gmsh.merge(file_path)
        
        # Extract Face Metadata
        faces_metadata = []
        
        # Get all 2D entities (Surfaces)
        dim_tags = gmsh.model.getEntities(dim=2)
        
        for dim, tag in dim_tags:
            # Get Type
            type_str = gmsh.model.getType(dim, tag)
            
            # Get Bounding Box Center
            bbox = gmsh.model.getBoundingBox(dim, tag)
            center = [
                (bbox[0] + bbox[3]) / 2,
                (bbox[1] + bbox[4]) / 2,
                (bbox[2] + bbox[5]) / 2
            ]
            
            # Try to get Normal
            normal = None
            try:
                # This only works if parametrization is available
                # Use a point in the middle of parameter space if possible
                # Simple fallback: use bounding box center if on surface?
                # gmsh.model.getCurvature needs parametric coordinates (u,v)
                # We can try to get them from the center point using getParametrization?
                # Or just skip normal for now if it's too complex without meshing
                pass
            except:
                pass

            faces_metadata.append(FaceInfo(
                id=tag,
                type=type_str,
                area=0.0, 
                center=center,
                normal=normal
            ))
            
        gmsh.finalize()
        
        return GeometryMetadata(
            faces=faces_metadata,
            message=f"Identified {len(faces_metadata)} faces"
        )

    except Exception as e:
        if gmsh.isInitialized():
            gmsh.finalize()
        raise HTTPException(status_code=500, detail=f"Metadata extraction failed: {str(e)}")

@router.post("/generate-mesh", response_model=MeshInfo)
async def generate_mesh(filename: str, mesh_size: float = 0.5):
    """
    Generate a 3D tetrahedral mesh from the uploaded geometry using Gmsh.
    Also extracts B-Rep face metadata for STEP/IGES files.
    """
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    try:
        # Initialize Gmsh
        if not gmsh.isInitialized():
            gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.model.add("Model")

        # Merge the geometry file
        # Gmsh supports STL, STEP, IGES, etc.
        gmsh.merge(file_path)

        # Extract Face Metadata (B-Rep)
        # Only works effectively for STEP/IGES (B-Rep formats)
        # For STL, faces are discrete triangles, but Gmsh might group them if they form a surface.
        faces_metadata = []
        
        # Get all 2D entities (Surfaces)
        # dimTags is list of (dim, tag)
        dim_tags = gmsh.model.getEntities(dim=2)
        
        for dim, tag in dim_tags:
            # Get Type
            type_str = gmsh.model.getType(dim, tag)
            
            # Get Mass Properties (Area, Center of Mass)
            # mass, CoM, inertia
            # Note: For surfaces, mass is Area
            props = gmsh.model.occ.getMass(dim, tag) if "OpenCASCADE" in gmsh.model.getType(dim, tag) else None
            
            # Fallback for non-OCC models (like STL) using mesh-based mass calculation
            # But getMass might work if mesh is generated?
            # Let's try general getMass logic or bounding box
            
            bbox = gmsh.model.getBoundingBox(dim, tag)
            center = [
                (bbox[0] + bbox[3]) / 2,
                (bbox[1] + bbox[4]) / 2,
                (bbox[2] + bbox[5]) / 2
            ]
            
            # Try to get Normal at Center (Parametric center)
            # We need UV bounds
            normal = None
            try:
                # This only works if parametrization is available (STEP/IGES)
                u_min, u_max, v_min, v_max = gmsh.model.getParametrizationBounds(dim, tag)
                u_mid = (u_min + u_max) / 2
                v_mid = (v_min + v_max) / 2
                _, _, _, nx, ny, nz = gmsh.model.getCurvature(dim, tag, [u_mid, v_mid])
                normal = [nx, ny, nz]
            except:
                pass

            faces_metadata.append(FaceInfo(
                id=tag,
                type=type_str,
                area=0.0, # Placeholder, calculation is complex without OCC kernel fully active
                center=center,
                normal=normal
            ))

        # Set mesh size
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)

        # Generate 3D mesh
        gmsh.model.mesh.generate(3)
        
        # Save mesh to file for consistent reuse in solver
        msh_path = file_path + ".msh"
        gmsh.write(msh_path)
        print(f"Mesh saved to {msh_path}")

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
             print("Warning: No 3D elements generated. Mesh might be surface only or failed.")
        
        gmsh.finalize()

        return MeshInfo(
            nodes=nodes,
            elements=elements,
            faces=faces_metadata,
            status="success",
            message=f"Generated {len(nodes)} nodes, {len(elements)} elements, and identified {len(faces_metadata)} faces"
        )

    except Exception as e:
        if gmsh.isInitialized():
            gmsh.finalize()
        print(f"Meshing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Meshing failed: {str(e)}")
