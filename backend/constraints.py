from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Literal, Optional, Union

router = APIRouter()

class Vector3(BaseModel):
    x: float
    y: float
    z: float

class BoundaryCondition(BaseModel):
    id: str
    name: str
    type: Literal["fixed", "displacement", "force", "pressure", "temperature"]
    applicationType: Literal["face", "edge", "vertex"] # Frontend uses applicationType
    entityIndex: int # Frontend uses entityIndex
    
    # Optional parameters
    value: Optional[Union[Vector3, float, List[float]]] = None 
    force: Optional[Union[Vector3, dict, List[float]]] = None # Frontend sends 'force' for force type
    displacement: Optional[Union[Vector3, dict, List[float]]] = None # Frontend sends 'displacement'
    description: Optional[str] = None
    
    # Backward compatibility (optional)
    targetType: Optional[Literal["face", "edge", "vertex"]] = None
    targetIds: Optional[List[int]] = None

class SimulationSetup(BaseModel):
    material_id: str
    mesh_size: float
    boundary_conditions: List[BoundaryCondition]

@router.post("/validate-setup")
async def validate_setup(setup: SimulationSetup):
    """
    Validate the simulation setup before solving.
    Checks for:
    1. Valid material
    2. Existence of constraints (Fixed Support)
    3. Existence of loads (Force/Pressure)
    """
    errors = []
    
    # 1. Check material (In a real app, we would check if ID exists in DB)
    if not setup.material_id:
        errors.append("Material is not selected.")

    # 2. Check Boundary Conditions
    bcs = setup.boundary_conditions
    
    has_fixed = any(bc.type == "fixed" for bc in bcs)
    has_load = any(bc.type in ["force", "pressure"] for bc in bcs)
    
    if not has_fixed:
        errors.append("Missing Fixed Support. The model is unconstrained and cannot be solved.")
        
    if not has_load:
        errors.append("Missing Load (Force or Pressure). The result will be zero.")

    if errors:
        return {"valid": False, "errors": errors}
    
    return {"valid": True, "message": "Setup is valid for simulation."}
