/**
 * 收敛检查的显示逻辑（纯函数，不依赖 React）。
 *
 * 为什么要有这一层
 * ----------------
 * 后端已经保证了"数值算得对"，但界面上仍然有三种典型错误，而且都不会报错：
 *
 * 1. **字段名对不上**（后端 `order_estimator` vs 前端 `orderEstimator`）——
 *    会静默地少显示一项，用户以为"这一项本来就没有"；
 * 2. **把缺失显示成 0**——本项目吃过一次亏：`formatFrequency(null)` 曾经返回
 *    `'0 Hz'`（因为 `Number(null) === 0`），于是"数据缺失"被显示成"刚体模态"。
 *    收敛检查里的 0 是有意义的（比如"最后一级变化 0%"），所以必须先把
 *    "缺失"和"0"分开；
 * 3. **把四态结论压成两种颜色**——`marginal`（在趋稳但还没到阈值）既不是
 *    "通过"也不是"失败"。压成绿/红会误导用户，而这个状态恰恰是最常见的。
 *
 * 这些函数可以被 node 直接执行（见 tools/tasks.py 的 verify 自检），
 * 因此本模块**不得 import 其他 .ts 模块**：Node ESM 不做扩展名补全。
 */

/** 与后端 `convergence_study.StudyLevel` 对应。 */
export interface StudyLevel {
  label: string;
  meshSize: number;
  elements: number;
  nodes: number;
  quantities: Record<string, number>;
}

/** 与后端 `convergence.assess()` 的返回对应（只取界面要用的字段）。 */
export interface StudyAssessment {
  label: string;
  values: number[];
  differences: number[];
  levels: number;
  monotone: boolean;
  observedOrder: number | null;
  orderEstimator: string;
  extrapolatedLimit: number | null;
  lastRelativeChange: number | null;
  converged: boolean;
  verdict: string;
  mode: string | null;
}

/** 已归一化、可直接渲染的收敛检查结果。 */
export interface ConvergenceStudy {
  status: string;
  analysisType: string;
  primary: string;
  quantities: string[];
  labels: Record<string, string>;
  levels: StudyLevel[];
  assessments: Record<string, StudyAssessment>;
  tolerance: number;
  verdict: string;
  notes: string[];
  warnings: string[];
}

/** 考察量的显示单位与换算系数（后端一律返回 SI）。 */
export const QUANTITY_DISPLAY: Record<string, { unit: string; scale: number }> = {
  // 应力：Pa → MPa（与结果云图一致）
  max_stress: { unit: 'MPa', scale: 1e-6 },
  // 位移：m → mm
  max_displacement: { unit: 'mm', scale: 1e3 },
  max_heat_flux: { unit: 'W/m²', scale: 1 },
  // 温度刻意用 K 而不是 °C：结论里的"相对变化"是后端按**绝对温度**算的，
  // 换成 °C 之后用户自己一算会对不上（0 °C 不是 0 绝对温度）。
  max_temperature: { unit: 'K', scale: 1 },
  first_elastic_frequency: { unit: 'Hz', scale: 1 },
};

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

function numberArrayOrNull(raw: unknown): number[] | null {
  if (!Array.isArray(raw)) return null;
  const numbers: number[] = [];
  for (const item of raw) {
    const parsed = finiteNumber(item);
    if (parsed === null) return null;
    numbers.push(parsed);
  }
  return numbers;
}

function parseStringArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === 'string');
}

function parseLabels(raw: unknown): Record<string, string> {
  const labels: Record<string, string> = {};
  if (raw === null || typeof raw !== 'object') return labels;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'string') labels[key] = value;
  }
  return labels;
}

function parseLevels(raw: unknown): StudyLevel[] | null {
  if (!Array.isArray(raw)) return null;
  const levels: StudyLevel[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== 'object') return null;
    const record = item as Record<string, unknown>;
    const elements = finiteNumber(record.elements);
    const meshSize = finiteNumber(record.mesh_size);
    if (elements === null || meshSize === null) return null;

    const quantities: Record<string, number> = {};
    const rawQuantities = record.quantities;
    if (rawQuantities === null || typeof rawQuantities !== 'object') return null;
    for (const [key, value] of Object.entries(rawQuantities as Record<string, unknown>)) {
      const parsed = finiteNumber(value);
      if (parsed !== null) quantities[key] = parsed;
    }

    levels.push({
      label: typeof record.label === 'string' ? record.label : `mesh_size=${meshSize}`,
      meshSize,
      elements,
      nodes: finiteNumber(record.nodes) ?? 0,
      quantities,
    });
  }
  return levels;
}

function parseAssessments(raw: unknown): Record<string, StudyAssessment> | null {
  if (raw === null || typeof raw !== 'object') return null;
  const assessments: Record<string, StudyAssessment> = {};
  for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
    if (value === null || typeof value !== 'object') continue;
    const record = value as Record<string, unknown>;
    const values = numberArrayOrNull(record.values);
    const differences = numberArrayOrNull(record.differences);
    if (values === null || differences === null) continue;
    assessments[name] = {
      label: typeof record.label === 'string' ? record.label : name,
      values,
      differences,
      levels: finiteNumber(record.levels) ?? values.length,
      monotone: record.monotone === true,
      observedOrder: finiteNumber(record.observed_order),
      orderEstimator:
        typeof record.order_estimator === 'string' ? record.order_estimator : '',
      extrapolatedLimit: finiteNumber(record.extrapolated_limit),
      lastRelativeChange: finiteNumber(record.last_relative_change),
      converged: record.converged === true,
      verdict: typeof record.verdict === 'string' ? record.verdict : '',
      mode: typeof record.mode === 'string' ? record.mode : null,
    };
  }
  return assessments;
}

/**
 * 把后端响应归一化成可渲染的结构；不可用时返回 `null`。
 *
 * `insufficient`（级数不足）**是**一个有效结果，不是错误：后端会明确说
 * "无法判断"，界面必须照原样显示这个结论，而不是当成失败藏起来。
 */
export function toConvergenceStudy(raw: unknown): ConvergenceStudy | null {
  if (raw === null || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;

  if (record.status === 'failed') return null;
  const status = typeof record.status === 'string' ? record.status : '';
  if (!status) return null;

  const levels = parseLevels(record.levels);
  if (levels === null) return null;
  const assessments = parseAssessments(record.assessments);
  if (assessments === null) return null;

  // `insufficient` 允许 0 级（第一级就超单元数上限）；其它状态必须有级别，
  // 否则"结论"就没有任何数据支撑。
  if (status !== 'insufficient' && levels.length === 0) return null;

  return {
    status,
    analysisType: typeof record.analysis_type === 'string' ? record.analysis_type : '',
    primary: typeof record.primary === 'string' ? record.primary : '',
    quantities: parseStringArray(record.quantities),
    labels: parseLabels(record.labels),
    levels,
    assessments,
    tolerance: finiteNumber(record.tolerance) ?? 0.05,
    verdict: typeof record.verdict === 'string' ? record.verdict : '',
    notes: parseStringArray(record.notes),
    warnings: parseStringArray(record.warnings),
  };
}

/** 数字的通用显示：保留有效数字，缺失显示 `—`（**不是** 0）。 */
export function formatNumber(value: unknown, digits = 4): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return '—';
  if (parsed === 0) return '0';
  const magnitude = Math.abs(parsed);
  if (magnitude >= 1e5 || magnitude < 1e-3) return parsed.toExponential(2);
  return parsed.toPrecision(digits);
}

/** 按考察量的物理含义换算并带上单位；缺失显示 `—`。 */
export function formatQuantity(value: unknown, name: string): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return '—';
  const display = QUANTITY_DISPLAY[name];
  if (!display) return formatNumber(parsed);
  return `${formatNumber(parsed * display.scale)} ${display.unit}`;
}

/** 相对变化（0.192 → `19.2%`）；缺失显示 `—`。 */
export function formatPercent(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return '—';
  return `${(parsed * 100).toFixed(2)}%`;
}

/** 观测收敛阶；`null`（无法判断）与 0 必须区分开。 */
export function formatOrder(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return '无法判断';
  return parsed.toFixed(2);
}

/** 考察量的显示名（后端给了就用后端的）。 */
export function quantityLabel(study: ConvergenceStudy | null, name: string): string {
  if (study && study.labels && typeof study.labels[name] === 'string') {
    return study.labels[name];
  }
  return name;
}

/** 状态徽标的文案与色调（四态，**不能**压成两种）。 */
export function describeStudyStatus(status: unknown): {
  label: string;
  tone: 'good' | 'warn' | 'bad' | 'info';
} {
  switch (status) {
    case 'converged':
      return { label: '已收敛', tone: 'good' };
    case 'marginal':
      return { label: '趋稳但未达阈值', tone: 'warn' };
    case 'not-converged':
      return { label: '尚未收敛', tone: 'bad' };
    case 'insufficient':
      return { label: '级数不足，无法判断', tone: 'info' };
    default:
      return { label: '未知状态', tone: 'info' };
  }
}

/**
 * 一个考察量的折线图数据点（`x`、`y` 都已归一化到 `[0, 1]`）。
 *
 * 横轴用**实测平均单元尺寸** `h ∝ N^(-1/3)` 的对数，而不是"第几级"：
 * 各级的实际加密幅度并不相等（见后端结论里的"实际加密比"），按级数画
 * 会把不等距的点画成等距，看起来像一条平滑的收敛曲线，其实是在骗人。
 *
 * 返回空数组表示无法作图（数据不足或全部相同）——界面据此不画图，
 * 而不是画一条平线（平线会被读成"完全收敛"）。
 */
export function trendPoints(
  study: ConvergenceStudy | null,
  name: string,
): Array<{ x: number; y: number; value: number; elements: number }> {
  if (!study || study.levels.length === 0) return [];
  const points: Array<{ x: number; y: number; value: number; elements: number }> = [];
  for (const level of study.levels) {
    const value = finiteNumber(level.quantities[name]);
    if (value === null) return [];
    points.push({ x: 0, y: 0, value, elements: level.elements });
  }
  if (points.length < 2) return [];

  // h ∝ N^(-1/3)：取对数后就是 -ln(N)/3。注意它的**符号**：网格越粗
  // （N 越小），这个值越大。所以下面归一化时要反过来映射，才能让"最粗的
  // 网格在左（x=0）、最细的在右（x=1）"，与界面上的标注一致。
  const logH = points.map(point => -Math.log(Math.max(point.elements, 1)) / 3);
  const xMin = Math.min(...logH);
  const xMax = Math.max(...logH);
  const values = points.map(point => point.value);
  const valueMin = Math.min(...values);
  const valueMax = Math.max(...values);

  for (let index = 0; index < points.length; index += 1) {
    // x：最粗的网格在 0，最细的在 1
    points[index].x = xMax > xMin ? (xMax - logH[index]) / (xMax - xMin) : 0;
    // y：数值最小的画在下方，留 10% 上下边距
    points[index].y = valueMax > valueMin
      ? 0.1 + 0.8 * (points[index].value - valueMin) / (valueMax - valueMin)
      : 0.5;
  }
  return points;
}

/**
 * 一行摘要：`最低 0.27 · 均值 0.76 …` 那种风格的紧凑信息。
 *
 * 对收敛检查，最该先看的是**主考察量的最后一级相对变化**——它直接回答
 * "再加密结果还会不会明显变"。
 */
export function describeStudySummary(study: ConvergenceStudy | null): string {
  if (!study) return '尚未检查';
  if (study.levels.length === 0) return '没有取得任何网格级别';
  const assessment = study.assessments[study.primary];
  const parts = [`${study.levels.length} 级网格`];
  if (assessment) {
    parts.push(`阶数 ${formatOrder(assessment.observedOrder)}`);
    if (assessment.lastRelativeChange !== null) {
      parts.push(`最后一级变化 ${formatPercent(assessment.lastRelativeChange)}`);
    }
  }
  return parts.join(' · ');
}

/** 逐级数表的一行（界面直接渲染）。 */
export function studyRows(study: ConvergenceStudy | null): Array<{
  label: string;
  elements: number;
  formatted: Record<string, string>;
}> {
  if (!study) return [];
  return study.levels.map(level => {
    const formatted: Record<string, string> = {};
    for (const name of study.quantities) {
      formatted[name] = formatQuantity(level.quantities[name], name);
    }
    return { label: level.label, elements: level.elements, formatted };
  });
}

/** 结论里必须出现的限定语（用于自检与界面提示）。 */
export const HONESTY_MARKERS: string[] = [
  '自收敛',
  '不能',
  '应力奇异',
];
