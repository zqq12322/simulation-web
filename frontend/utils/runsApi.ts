/**
 * 求解记录的读取与归一化（纯函数，不依赖 React）。
 *
 * 本模块刻意**只做防御性映射**，不做单位换算、不格式化时间：
 *
 * - 单位换算复用 `convergenceStudy.ts` 的 `formatQuantity`（同一套考察量、
 *   同一张单位表，界面上的数字必须一致）；
 * - 时间格式化复用 `projectsApi.ts` 的 `formatCreatedAt`（非法时间戳显示占位符
 *   这条规则已经在那边钉过一次）。
 *
 * 为什么不在本模块里 import 它们：本模块要能被 **node 直接执行**（见
 * tools/tasks.py 的 verify 自检），而 Node 的 ESM 解析器不做扩展名补全——
 * 跨模块相对导入必须写 `./xxx.ts`，那是 TS/Vite 与 Node 不一致的地方。
 * 让"展示"留在组件里（组件可以随便 import），纯逻辑留在本模块。
 */

/** 一条记录里的数值摘要。 */
export interface RunSummary {
  quantities: Record<string, number>;
  mesh: {
    meshSize?: number;
    elements?: number;
    nodes?: number;
  };
  warnings: string[];
  warningCount: number;
}

export interface RunRecord {
  id: string;
  projectId: string;
  analysisType: string;
  createdBy: string | null;
  createdAt: string;
  summary: RunSummary;
  /** 库里那条摘要的 JSON 坏了：界面要提示，而不是把空摘要当成"什么都没算出来" */
  summaryParseError: boolean;
}

export interface RunList {
  runs: RunRecord[];
  total: number;
  limit: number;
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

function parseQuantities(raw: unknown): Record<string, number> {
  const quantities: Record<string, number> = {};
  if (raw === null || typeof raw !== 'object') return quantities;
  for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
    const parsed = finiteNumber(value);
    // 非法值直接丢弃而不是写 0：0 在这里是一个有意义的数（应力可以是 0），
    // 把"缺失"显示成 0 会让历史记录看起来像算出了一个零结果。
    if (parsed !== null) quantities[name] = parsed;
  }
  return quantities;
}

function parseSummary(raw: unknown): RunSummary {
  const empty: RunSummary = { quantities: {}, mesh: {}, warnings: [], warningCount: 0 };
  if (raw === null || typeof raw !== 'object') return empty;
  const record = raw as Record<string, unknown>;

  const mesh: RunSummary['mesh'] = {};
  const rawMesh = record.mesh;
  if (rawMesh !== null && typeof rawMesh === 'object') {
    const meshRecord = rawMesh as Record<string, unknown>;
    const meshSize = finiteNumber(meshRecord.meshSize);
    const elements = finiteNumber(meshRecord.elements);
    const nodes = finiteNumber(meshRecord.nodes);
    if (meshSize !== null) mesh.meshSize = meshSize;
    if (elements !== null) mesh.elements = elements;
    if (nodes !== null) mesh.nodes = nodes;
  }

  const warnings = Array.isArray(record.warnings)
    ? record.warnings.filter((item): item is string => typeof item === 'string')
    : [];
  return {
    quantities: parseQuantities(record.quantities),
    mesh,
    warnings,
    warningCount: finiteNumber(record.warningCount) ?? warnings.length,
  };
}

/** 把一条后端记录归一化；`id` 或 `createdAt` 缺失时返回 `null`（丢弃坏记录）。 */
export function toRun(raw: unknown): RunRecord | null {
  if (raw === null || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.id !== 'string' || !record.id) return null;
  if (typeof record.createdAt !== 'string' || !record.createdAt) return null;
  return {
    id: record.id,
    projectId: typeof record.projectId === 'string' ? record.projectId : '',
    analysisType: typeof record.analysisType === 'string' ? record.analysisType : '',
    createdBy: typeof record.createdBy === 'string' ? record.createdBy : null,
    createdAt: record.createdAt,
    summary: parseSummary(record.summary),
    summaryParseError: record.summaryParseError === true,
  };
}

/**
 * 归一化列表响应。
 *
 * 坏记录逐条丢弃（不是整份失败）：一条坏数据不该让"历史记录"整块消失——
 * 那正是用户最需要看到的证据。
 */
export function toRunList(raw: unknown): RunList {
  const empty: RunList = { runs: [], total: 0, limit: 0 };
  if (raw === null || typeof raw !== 'object') return empty;
  const record = raw as Record<string, unknown>;
  const runs = Array.isArray(record.runs)
    ? record.runs.map(toRun).filter((item): item is RunRecord => item !== null)
    : [];
  return {
    runs,
    total: finiteNumber(record.total) ?? runs.length,
    limit: finiteNumber(record.limit) ?? 0,
  };
}

/** 该记录里出现的考察量名字（按后端给的顺序不可知，所以排序保证界面稳定）。 */
export function runQuantityNames(run: RunRecord | null): string[] {
  if (!run) return [];
  return Object.keys(run.summary.quantities).sort();
}

/** 网格信息的一行文本；没有网格信息时返回空串（界面据此不显示这一行）。 */
export function describeRunMesh(run: RunRecord | null): string {
  if (!run) return '';
  const { meshSize, elements, nodes } = run.summary.mesh;
  const parts: string[] = [];
  if (typeof meshSize === 'number') parts.push(`网格 ${meshSize}`);
  if (typeof elements === 'number') parts.push(`${elements} 单元`);
  if (typeof nodes === 'number') parts.push(`${nodes} 节点`);
  return parts.join(' · ');
}

/** 分析类型的中文名（未知类型原样返回，不编造）。 */
export function describeAnalysisType(analysisType: unknown): string {
  switch (analysisType) {
    case 'structural':
      return '结构静力';
    case 'thermal':
      return '稳态热传导';
    case 'modal':
      return '模态';
    default:
      return typeof analysisType === 'string' && analysisType ? analysisType : '未知类型';
  }
}

/** 有降级/忽略的边界条件时给出的提示；没有则返回 `null`。 */
export function describeRunWarnings(run: RunRecord | null): string | null {
  if (!run) return null;
  const count = run.summary.warningCount;
  if (!count) return null;
  return count === 1
    ? '求解时有 1 条警告（边界条件被忽略或降级）'
    : `求解时有 ${count} 条警告（边界条件被忽略或降级）`;
}

/** 记录数是否已达上限（界面据此提示"更早的记录已被裁掉"）。 */
export function historyIsTrimmed(list: RunList | null): boolean {
  if (!list) return false;
  return list.limit > 0 && list.total >= list.limit;
}
