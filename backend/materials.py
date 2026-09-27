from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Literal, Optional

router = APIRouter()

class Material(BaseModel):
    id: str
    name: str
    density: float  # kg/m^3
    youngsModulus: float  # Pa
    poissonsRatio: float
    color: str
    type: Literal['metal', 'plastic', 'concrete', 'wood', 'custom'] = 'metal'
    description: Optional[str] = None

# Predefined materials database (in-memory for now)
# Real implementation would fetch from a database
MATERIALS_DB = [
    Material(
        id="structural_steel",
        name="Structural Steel",
        density=7850,
        youngsModulus=2.0e11,
        poissonsRatio=0.3,
        color="#808080",
        type="metal",
        description="Standard structural steel for general construction"
    ),
    Material(
        id="aluminum_alloy",
        name="Aluminum Alloy",
        density=2700,
        youngsModulus=6.9e10,
        poissonsRatio=0.33,
        color="#C0C0C0",
        type="metal",
        description="Lightweight aluminum alloy 6061-T6"
    ),
    Material(
        id="copper",
        name="Copper",
        density=8960,
        youngsModulus=1.2e11,
        poissonsRatio=0.34,
        color="#B87333",
        type="metal",
        description="Pure copper with high conductivity"
    ),
    Material(
        id="titanium",
        name="Titanium",
        density=4500,
        youngsModulus=1.1e11,
        poissonsRatio=0.32,
        color="#878681",
        type="metal",
        description="High strength-to-weight ratio titanium alloy"
    ),
     Material(
        id="abs_plastic",
        name="ABS Plastic",
        density=1040,
        youngsModulus=2.3e9,
        poissonsRatio=0.35,
        color="#FFFFE0",
        type="plastic",
        description="Common thermoplastic polymer"
    )
]

@router.get("/materials", response_model=List[Material])
async def get_materials():
    """Get all available materials"""
    return MATERIALS_DB

@router.get("/materials/{material_id}", response_model=Material)
async def get_material(material_id: str):
    """Get a specific material by ID"""
    material = next((m for m in MATERIALS_DB if m.id == material_id), None)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return material

@router.post("/materials", response_model=Material)
async def create_material(material: Material):
    """Create a custom material (session-based)"""
    # Check if ID already exists
    if any(m.id == material.id for m in MATERIALS_DB):
        raise HTTPException(status_code=400, detail="Material ID already exists")
    
    MATERIALS_DB.append(material)
    return material
