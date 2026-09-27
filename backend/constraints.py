from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict
from typing import List, Literal, Optional, Union

from auth import require_user

#: 整个 router 都要求登录。用**路由级依赖**而不是给每个端点加参数：
#: 端点本身并不需要知道「你是谁」，而且 40 多个既有测试是**直接调用端点函数**的
#: （不经 HTTP），逐个加参数会让它们全部失效。
router = APIRouter(dependencies=[Depends(require_user)])


class Vector3(BaseModel):
    x: float
    y: float
    z: float

class BoundaryCondition(BaseModel):
    """
    边界条件。

    ``extra="forbid"`` 是刻意为之：项目历史上因为模型里**缺少** ``pressure`` /
    ``temperature`` 字段，Pydantic 把前端传来的值静默丢掉了，结果「压力」被
    当成零载荷求解却不报错。宁可返回 422 大声失败，也不要静默算出错误结果。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["fixed", "displacement", "force", "pressure", "temperature"]
    applicationType: Literal["face", "edge", "vertex"] # Frontend uses applicationType
    entityIndex: int # Frontend uses entityIndex
    color: Optional[str] = None # 前端用于 3D 可视化的颜色

    # Optional parameters
    value: Optional[Union[Vector3, float, List[float]]] = None 
    force: Optional[Union[Vector3, dict, List[float]]] = None # Frontend sends 'force' for force type
    displacement: Optional[Union[Vector3, dict, List[float]]] = None # Frontend sends 'displacement'
    pressure: Optional[float] = None # Frontend sends 'pressure' for pressure type
    temperature: Optional[float] = None # Frontend sends 'temperature'
    fixedX: Optional[bool] = None # displacement 类型：是否约束该方向
    fixedY: Optional[bool] = None
    fixedZ: Optional[bool] = None
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
