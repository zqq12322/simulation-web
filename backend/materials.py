from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Literal, Optional

from material_store import get_store

router = APIRouter()

class Material(BaseModel):
    id: str
    name: str
    density: float  # kg/m^3
    youngsModulus: float  # Pa
    poissonsRatio: float
    color: str
    type: Literal['metal', 'plastic', 'concrete', 'wood', 'custom'] = 'metal'
    #: 热导率 W/(m·K)，稳态热传导分析需要。历史数据可能没有该字段，
    #: 因此允许为空——传热求解器会在缺失时给出明确错误而不是当成 0。
    thermalConductivity: Optional[float] = None
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
        thermalConductivity=50.0,  # W/(m*K)
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
        thermalConductivity=167.0,  # W/(m*K)
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
        thermalConductivity=401.0,  # W/(m*K)
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
        thermalConductivity=22.0,  # W/(m*K)
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
        thermalConductivity=0.2,  # W/(m*K)
        color="#FFFFE0",
        type="plastic",
        description="Common thermoplastic polymer"
    )
]

@router.get("/materials", response_model=List[Material])
async def get_materials():
    """
    全部可用材料 = **内置材料（代码里定义）** + **自定义材料（SQLite 持久化）**。

    内置材料永远来自代码，因此即使数据库损坏也仍然可用。
    """
    custom = [Material(**item) for item in get_store().list_custom()]
    return MATERIALS_DB + custom

@router.get("/materials/{material_id}", response_model=Material)
async def get_material(material_id: str):
    """按 ID 取材料（先查内置，再查持久化的自定义材料）"""
    material = next((m for m in MATERIALS_DB if m.id == material_id), None)
    if material is not None:
        return material

    stored = get_store().get_custom(material_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return Material(**stored)

@router.post("/materials", response_model=Material)
async def create_material(material: Material):
    """
    新建自定义材料并**持久化**（重启后仍在）。

    此前材料只存在内存列表里，重启即丢；多人共用一台机器时也互相覆盖。
    """
    # 内置材料 ID 不允许被覆盖
    if any(m.id == material.id for m in MATERIALS_DB):
        raise HTTPException(
            status_code=400,
            detail=f"材料 ID 与内置材料冲突：{material.id}",
        )

    try:
        saved = get_store().add(material)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Material(**saved)
