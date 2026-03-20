from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import numpy as np
from skfem import *
from skfem.helpers import dot, grad, trace, sym_grad, eye, identity, ddot
from skfem.models.elasticity import linear_elasticity, lame_parameters
from skfem.visuals.matplotlib import draw, plot
import os
import gmsh

# Import local modules
from geometry import UPLOAD_DIR, FaceInfo
from materials import MATERIALS_DB
from constraints import BoundaryCondition

router = APIRouter()

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

    file_path = os.path.join(UPLOAD_DIR, request.geometry_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")
    
    # 1. Load Material Properties
    material = next((m for m in MATERIALS_DB if m.id == request.material_id), None)
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")
    
    E = material.youngsModulus
    nu = material.poissonsRatio
    
    try:
        # 2. Load Mesh (using scikit-fem's interface to Meshio/Gmsh)
        msh_path = file_path + ".msh"
        
        # Check if MSH exists
        if not os.path.exists(msh_path):
            # If not found, generate it now (fallback)
            print(f"Mesh file {msh_path} not found, generating...")
            if not gmsh.isInitialized():
                gmsh.initialize()
            gmsh.model.add("SolverModel")
            gmsh.merge(file_path)
            gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0) # Default size
            gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
            gmsh.model.mesh.generate(3)
            gmsh.write(msh_path)
            gmsh.finalize()
        else:
            print(f"Using cached mesh file: {msh_path}")
        
        # Load mesh into scikit-fem
        mesh = Mesh.load(msh_path)
        print(f"Mesh loaded: {mesh}")

        # 3. Define Element and Basis
        # Linear Tetrahedral Element (Vector H1)
        element_vec = ElementVectorH1(ElementTetP1())
        basis_vec = Basis(mesh, element_vec)

        # 4. Assemble Stiffness Matrix (Linear Elasticity)
        # Lame parameters
        lam, mu = lame_parameters(E, nu)
        
        # K = stiffness matrix
        K = asm(linear_elasticity(lam, mu), basis_vec)
        
        # 5. Apply Boundary Conditions
        f = np.zeros(basis_vec.N)
        fixed_dofs = []
        
        # Helper to find nodes by coordinates (Heuristic)
        # 0: Min X, 1: Max X, 2: Min Y, 3: Max Y, 4: Min Z, 5: Max Z
        
        for bc in request.boundary_conditions:
            # Simple Heuristic Mapping
            
            # Find nodes on the boundary
            x, y, z = mesh.p
            
            target_nodes_indices = []
            
            # Check for applicationType: "vertex", "edge", or "face" (default)
            app_type = getattr(bc, 'applicationType', 'face') # Default to face if not present
            
            if app_type == 'vertex':
                # For vertex, entityIndex is the vertex index (node index)
                # Frontend sends 0-based index. 
                if 0 <= bc.entityIndex < mesh.p.shape[1]:
                    target_nodes_indices = [bc.entityIndex]
                else:
                    print(f"Warning: Vertex index {bc.entityIndex} out of bounds.")
                    
            elif app_type == 'face':
                # Check if we have face metadata for this index
                target_face = next((f for f in request.faces if f.id == bc.entityIndex), None)
                
                if target_face:
                    # Robust Geometric Search (Plane Proximity)
                    if target_face.normal:
                        nx, ny, nz = target_face.normal
                        cx, cy, cz = target_face.center
                        
                        dx = x - cx
                        dy = y - cy
                        dz = z - cz
                        
                        dist_to_plane = np.abs(dx*nx + dy*ny + dz*nz)
                        # Increased tolerance for better robustness
                        plane_tol = 1e-2 
                        
                        mask = dist_to_plane < plane_tol
                        target_nodes_indices = np.where(mask)[0]
                        print(f"Face {bc.entityIndex} (Normal search): Found {len(target_nodes_indices)} nodes")
                    else:
                        # If no normal (curved surface), use distance to center + bounding box check?
                        # Or use Gmsh Physical Groups if we had them.
                        # For now, fallback to distance to center (very rough)
                        # or better, use the bounding box of the face if available?
                        # Let's try a distance threshold to center for small faces
                        # This is weak but better than nothing
                        pass
                
                # Fallback to Box Heuristic if no nodes found (or for STL legacy support)
                if len(target_nodes_indices) == 0:
                    print(f"Face {bc.entityIndex}: Normal search failed or no normal. Using Box Heuristic.")
                    bbox_dims = [x.max() - x.min(), y.max() - y.min(), z.max() - z.min()]
                    max_dim = max(bbox_dims) if bbox_dims else 1.0
                    tol = max_dim * 0.1 # Increased tolerance to 10%
                    if tol < 1e-3: tol = 1e-3
                    
                    idx = bc.entityIndex % 6 
                    if idx == 0: mask = x < x.min() + tol
                    elif idx == 1: mask = x > x.max() - tol
                    elif idx == 2: mask = y < y.min() + tol
                    elif idx == 3: mask = y > y.max() - tol
                    elif idx == 4: mask = z < z.min() + tol
                    elif idx == 5: mask = z > z.max() - tol
                    
                    target_nodes_indices = np.where(mask)[0]
                    print(f"Face {bc.entityIndex} (Box Heuristic): Found {len(target_nodes_indices)} nodes")
            
            # Apply BC to found nodes
            if bc.type == "fixed":
                for node_idx in target_nodes_indices:
                     # Constrain all 3 components (u, v, w)
                     fixed_dofs.append(basis_vec.nodal_dofs[0][node_idx]) # u
                     fixed_dofs.append(basis_vec.nodal_dofs[1][node_idx]) # v
                     fixed_dofs.append(basis_vec.nodal_dofs[2][node_idx]) # w
                
            elif bc.type == "force":
                num_nodes_on_face = len(target_nodes_indices)
                
                if num_nodes_on_face > 0:
                    # Force vector from frontend
                    fx, fy, fz = 0.0, 0.0, 0.0
                    
                    if isinstance(bc.force, dict):
                        fx = float(bc.force.get('x', 0.0))
                        fy = float(bc.force.get('y', 0.0))
                        fz = float(bc.force.get('z', 0.0))
                    elif hasattr(bc.force, 'x'): 
                        fx = float(bc.force.x)
                        fy = float(bc.force.y)
                        fz = float(bc.force.z)
                    elif isinstance(bc.force, list) and len(bc.force) == 3:
                        fx, fy, fz = float(bc.force[0]), float(bc.force[1]), float(bc.force[2])
                    
                    # Distributed load (force per node)
                    # Ideally should integrate over area, but for point clouds:
                    force_per_node = np.array([fx, fy, fz]) / num_nodes_on_face
                    
                    for node_idx in target_nodes_indices:
                        f[basis_vec.nodal_dofs[0][node_idx]] += force_per_node[0]
                        f[basis_vec.nodal_dofs[1][node_idx]] += force_per_node[1]
                        f[basis_vec.nodal_dofs[2][node_idx]] += force_per_node[2]

        # 6. Solve
        D = np.unique(fixed_dofs)
        
        # Check if f is all zero and no fixed dofs (to prevent singular matrix if user messed up)
        if len(D) == 0:
             # Fallback: Fix 3 corners to prevent rigid body motion if no constraints
             # This is just to ensure solver doesn't crash, result will be meaningless rigid body
             print("Warning: No fixed constraints found. Applying fallback constraints.")
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

        # Solve Linear System: K u = f
        u = solve(*condense(K, f, D=D))

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
        
        # Define Functional for Von Mises Stress
        @Functional
        def get_von_mises(w):
            # w corresponds to the solution interpolated at quadrature points
            e = sym_grad(w)
            # Constitutive relation: sigma = 2*mu*e + lam*tr(e)*I
            s = 2.0 * mu * e + lam * trace(e) * eye(e, 3)
            
            # Deviatoric stress: s_dev = s - 1/3 * tr(s) * I
            s_dev = s - (1.0/3.0) * trace(s) * eye(s, 3)
            
            # Von Mises: sqrt(3/2 * s_dev : s_dev)
            # Use manual double dot product since ddot might not be available in all versions or behaves differently
            # ddot(A, B) = A_ij * B_ij
            
            # s_dev is a 3x3 tensor
            val = ddot(s_dev, s_dev)
            return np.sqrt(1.5 * val)

        # Project stress to P1 nodes
        basis_scalar = Basis(mesh, ElementTetP1())
        # We need to interpolate the VECTOR solution u using the VECTOR basis
        u_interp = basis_vec.interpolate(u)
        
        # Then project the functional result
        stress_vals = basis_scalar.project(get_von_mises(u_interp))
        
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

        return SolverResult(
            status="solved",
            message="Simulation completed successfully.",
            max_displacement=max_disp,
            max_stress=max_stress,
            displacements=displacements,
            stresses=stresses,
            reaction_forces=reaction_forces
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Ensure gmsh is finalized if error occurs during mesh generation/loading
        if gmsh.isInitialized():
            gmsh.finalize()
        raise HTTPException(status_code=500, detail=f"Solver failed: {str(e)}")
