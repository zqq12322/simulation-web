export interface Material {
  id: string;
  name: string;
  density: number;
  youngsModulus: number;
  poissonsRatio: number;
  color: string;
  type: 'metal' | 'plastic' | 'concrete' | 'wood' | 'custom';
}

export interface Project {
  id: string;
  title: string;
  description: string;
  createdAt: Date;
  simulationType?: 'CFD' | 'FEA' | 'Thermal' | 'General';
  isPrivate?: boolean;
  /**
   * 属主用户 id；`null` 表示**无主项目**（接上登录之前创建的遗留数据）。
   *
   * 无主项目对已登录用户可见但**不可改、不可删**，需要先调用
   * `POST /api/projects/{id}/claim` 认领。理由见 `backend/project_store.py`：
   * 初版做过"第一个注册的用户自动接管"，结果把开发者手工建的项目静默划给了
   * 测试账号（`tools/tasks.py verify`），用户下次登录就发现项目不见了。
   */
  ownerId?: string | null;
  /**
   * 是否保存过仿真配置（几何/材料/边界条件/网格与求解设置）。
   *
   * 注意这是**列表接口**给的标记，完整配置走 `GET /api/projects/{id}/setup`。
   */
  hasSetup?: boolean;
}

export type ViewState = 'dashboard' | 'workbench';
// 网格划分设置
export interface MeshSettings {
  id: string;
  name: string;
  meshType: 'tetrahedral' | 'hexahedral' | 'mixed';
  meshSize: number;
  refinementRegions: {
    entityType: 'face' | 'edge' | 'vertex';
    entityIndex: number;
    refinementLevel: number;
  }[];
  quality: number;
  // 'solved' is set by the workbench once a result exists, which switches the
  // 3D view into stress-colour mode.
  status: 'not_meshed' | 'meshing' | 'meshed' | 'failed' | 'solved';
}

// 求解器类型
export type SolverType = 'structural' | 'cfd' | 'thermal' | 'modal';

// 求解器设置
export interface SolverSettings {
  id: string;
  name: string;
  solverType: SolverType;
  solverName: string; // 具体求解器名称
  parameters: {
    [key: string]: number | string | boolean;
  };
  /** 几何坐标的长度单位。后端据此换算成米，结果一律为 SI（位移 m、应力 Pa）。 */
  lengthUnit?: 'm' | 'mm';
  status: 'not_configured' | 'configured' | 'solving' | 'solved' | 'failed';
}

// 更新SceneState，添加边界条件、网格设置和求解器设置
export interface SceneState {
  modelUrl: string | null;
  geometryName: string | null;
  selectedMaterial: Material | null;
  boundaryConditions: AnyBoundaryCondition[];
  meshSettings: MeshSettings | null;
  solverSettings: SolverSettings | null;
}

// 边界条件类型
export type BoundaryConditionType = 'fixed' | 'displacement' | 'force' | 'pressure' | 'temperature';

// 应用对象类型
export type ApplicationType = 'vertex' | 'edge' | 'face';

// 向量类型（用于表示力、位移等）
export interface Vector3 {
  x: number;
  y: number;
  z: number;
}

// 边界条件基础接口
export interface BoundaryCondition {
  id: string;
  name: string;
  type: BoundaryConditionType;
  applicationType: ApplicationType;
  entityIndex: number; // 应用对象的索引（点/边/面的索引）
  color: string; // 用于3D可视化的颜色
}

// 固定约束
export interface FixedConstraint extends BoundaryCondition {
  type: 'fixed';
  // 固定约束不需要额外参数，默认限制所有方向
}

// 位移约束
export interface DisplacementConstraint extends BoundaryCondition {
  type: 'displacement';
  displacement: Vector3; // 允许的位移
  fixedX: boolean; // 是否固定X方向
  fixedY: boolean; // 是否固定Y方向
  fixedZ: boolean; // 是否固定Z方向
}

// 力载荷
export interface ForceLoad extends BoundaryCondition {
  type: 'force';
  force: Vector3; // 力的大小和方向
}

// 压力载荷
export interface PressureLoad extends BoundaryCondition {
  type: 'pressure';
  pressure: number; // 压力值
}

// 温度条件
export interface TemperatureCondition extends BoundaryCondition {
  type: 'temperature';
  temperature: number; // 温度值
}

// 联合类型，包含所有边界条件类型
export type AnyBoundaryCondition = FixedConstraint | DisplacementConstraint | ForceLoad | PressureLoad | TemperatureCondition;

export interface Geometry {
  id: string;
  name: string;
  url: string;
  materialId: string | null;
  boundaryConditionIds: string[];
}

