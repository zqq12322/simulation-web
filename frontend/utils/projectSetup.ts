/**
 * 项目仿真配置（几何 / 材料 / 边界条件 / 网格与求解设置）的纯逻辑。
 *
 * 为什么需要它
 * ------------
 * 在此之前"项目"只是一个名字 + 描述：几何、材料、边界条件、网格与求解设置
 * **全在浏览器内存里**，关掉页面就没了。于是"项目"这个概念的承诺（下次接着做）
 * 实际上是空的——重新打开项目是一个空白工作台。
 *
 * 这个模块负责三件事，三件都容易写错，所以都放在能被 node 直接断言的地方：
 *
 * 1. **组装待保存的文档**：只放真正属于项目配置的状态，不放"当前选中了哪个面"
 *    这类瞬时 UI 状态。
 * 2. **判断"有没有变"**：自动保存必须能识别"没变"，否则每次重渲染都会写一次库。
 *    这里用**按键排序**的稳定序列化，避免对象键顺序不同造成的假变化。
 * 3. **恢复时校验**：存下来的文档可能是旧版前端写的，恢复时任何不认识的东西
 *    都要**被丢弃并报告**，而不是让界面进入一个半损坏的状态。
 */

import type {
  AnyBoundaryCondition,
  MeshSettings,
  Material,
  SolverSettings,
} from '../types';

/** 配置文档版本。形状变化时靠它迁移，而不是猜。 */
export const SETUP_VERSION = 1;

export interface ProjectSetup {
  version: number;
  geometryFilename: string | null;
  materialId: string | null;
  boundaryConditions: AnyBoundaryCondition[];
  meshSettings: Record<string, unknown> | null;
  solverSettings: Record<string, unknown> | null;
}

/** 组装配置文档时用到的界面状态。 */
export interface SetupSourceState {
  modelName: string | null;
  selectedMaterial: Material | null;
  boundaryConditions: AnyBoundaryCondition[];
  meshSettings: MeshSettings | null;
  solverSettings: SolverSettings | null;
}

/**
 * 稳定序列化：递归地把对象键排序后再 JSON。
 *
 * `JSON.stringify` 保留键的插入顺序，于是 `{a:1,b:2}` 与 `{b:2,a:1}` 会得到
 * 不同的字符串——自动保存的"变了吗"判断就会假阳性，每次重渲染都写一次库。
 */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) {
    return `[${value.map(item => stableStringify(item)).join(',')}]`;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return `{${keys.map(key => `${JSON.stringify(key)}:${stableStringify(record[key])}`).join(',')}}`;
}

/** 组装待保存的配置文档。 */
export function buildSetupPayload(state: SetupSourceState): ProjectSetup {
  return {
    version: SETUP_VERSION,
    geometryFilename: state.modelName || null,
    materialId: state.selectedMaterial?.id || null,
    // 深拷贝一层，避免把界面状态对象的引用交给调用方去序列化
    boundaryConditions: Array.isArray(state.boundaryConditions)
      ? JSON.parse(JSON.stringify(state.boundaryConditions))
      : [],
    meshSettings: state.meshSettings
      ? (JSON.parse(JSON.stringify(state.meshSettings)) as Record<string, unknown>)
      : null,
    solverSettings: state.solverSettings
      ? (JSON.parse(JSON.stringify(state.solverSettings)) as Record<string, unknown>)
      : null,
  };
}

/**
 * 用于"和上次保存的是否相同"比较的签名。
 *
 * 刻意**不含** `version`：版本只在形状变化时改，不应当触发一次自动保存。
 */
export function setupSignature(setup: ProjectSetup | null | undefined): string {
  if (!setup) return '';
  const { version: _version, ...rest } = setup;
  return stableStringify(rest);
}

/** 可恢复的界面状态（Workbench 直接把这些塞进各 setState）。 */
export interface RestoredSetup {
  geometryFilename: string | null;
  materialId: string | null;
  boundaryConditions: AnyBoundaryCondition[];
  meshSettings: MeshSettings | null;
  solverSettings: SolverSettings | null;
  /** 被丢弃/降级的内容说明；非空时界面应当展示出来 */
  warnings: string[];
}

/**
 * 把存下来的文档转成界面状态，并**报告所有被丢弃的东西**。
 *
 * 校验从宽（存下来的就是前端自己写的），但对"类型完全不对"的字段必须丢弃并
 * 报告：让界面进入半损坏状态（例如把字符串当成边界条件数组用）远比明确报错更糟。
 */
export function restoreSetup(raw: unknown): RestoredSetup {
  const warnings: string[] = [];
  const result: RestoredSetup = {
    geometryFilename: null,
    materialId: null,
    boundaryConditions: [],
    meshSettings: null,
    solverSettings: null,
    warnings,
  };

  const document = raw as Record<string, unknown> | null;
  if (!document || typeof document !== 'object') return result;

  const version = Number(document.version);
  if (Number.isFinite(version) && version !== SETUP_VERSION) {
    // 先只报告，不阻止恢复：未知版本里能认出来的字段仍然有用
    warnings.push(
      `配置版本为 ${version}，当前支持 ${SETUP_VERSION}；已尽力恢复可识别的部分。`
    );
  }

  if (typeof document.geometryFilename === 'string' && document.geometryFilename) {
    result.geometryFilename = document.geometryFilename;
  } else if (document.geometryFilename != null) {
    warnings.push('几何文件名格式不对，已忽略。');
  }

  if (typeof document.materialId === 'string' && document.materialId) {
    result.materialId = document.materialId;
  } else if (document.materialId != null) {
    warnings.push('材料 id 格式不对，已忽略。');
  }

  if (Array.isArray(document.boundaryConditions)) {
    result.boundaryConditions = document.boundaryConditions as AnyBoundaryCondition[];
  } else if (document.boundaryConditions != null) {
    warnings.push('边界条件不是数组，已忽略。');
  }

  if (document.meshSettings && typeof document.meshSettings === 'object'
      && !Array.isArray(document.meshSettings)) {
    result.meshSettings = document.meshSettings as MeshSettings;
  } else if (document.meshSettings != null) {
    warnings.push('网格设置格式不对，已忽略。');
  }

  if (document.solverSettings && typeof document.solverSettings === 'object'
      && !Array.isArray(document.solverSettings)) {
    result.solverSettings = document.solverSettings as SolverSettings;
  } else if (document.solverSettings != null) {
    warnings.push('求解设置格式不对，已忽略。');
  }

  return result;
}

/** 自动保存的状态机取值。 */
export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

/**
 * 保存状态 → 界面文案。
 *
 * **必须如实**：`error` 不能显示成"已保存"——用户会以为配置存下来了，
 * 下次打开才发现全丢了。
 */
export function describeSaveStatus(
  status: SaveStatus,
  detail?: { savedAt?: string | null; error?: string | null },
): string {
  switch (status) {
    case 'saving':
      return '保存中…';
    case 'saved': {
      const at = detail?.savedAt ? new Date(detail.savedAt) : null;
      if (!at || Number.isNaN(at.getTime())) return '已保存';
      return `已保存 ${at.toLocaleTimeString()}`;
    }
    case 'error':
      return detail?.error ? `保存失败：${detail.error}` : '保存失败';
    default:
      return '未修改';
  }
}

/** 项目是否"已配置"（用于仪表盘上的标记）。 */
export function describeSetupBadge(hasSetup: boolean | null | undefined): string {
  return hasSetup ? '已配置' : '空项目';
}
