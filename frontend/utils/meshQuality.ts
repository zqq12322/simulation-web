/**
 * 网格质量的显示逻辑（纯函数，不依赖 React）。
 *
 * 为什么单独成模块
 * ----------------
 * 「网格质量」这件事有三个地方可能出错，而它们在界面上长得一模一样：
 * 1. 后端算错（公式、单位、朝向）—— 由 `backend/tests/test_mesh_quality.py`
 *    的解析断言负责；
 * 2. 字段名对不上（后端 `edge_ratio_max` vs 前端 `edgeRatioMax`）—— 会静默地
 *    显示成"没有这一项"；
 * 3. 界面上把"空/缺失"画成"很好"。
 *
 * 第 3 类本项目已经吃过一次亏：`formatFrequency(null)` 曾经返回 `'0 Hz'`
 * （因为 `Number(null) === 0`），于是"数据缺失"被显示成"刚体模态"。
 * 所以这里所有格式化函数都**先判类型再转数字**，缺失一律显示 `—`，
 * 绝不显示 0。
 *
 * 这些函数可以被 node 直接执行（见 tools/tasks.py 的 verify 自检），
 * 因此本模块**不得 import 其他 .ts 模块**：Node ESM 不做扩展名补全。
 */

/** 后端 `/api/mesh-quality` 的质量统计量。 */
export interface QualityStats {
  min: number;
  max: number;
  mean: number;
  median: number;
  p05: number;
}

/** 直方图的一个箱（质量区间 [lo, hi) 内的单元数）。 */
export interface QualityBin {
  lo: number;
  hi: number;
  count: number;
}

/** 一个"最差单元"的明细。 */
export interface QualityElement {
  index: number;
  quality: number;
  volume: number;
  edgeRatio: number;
}

/** 已归一化、可直接渲染的网格质量。 */
export interface MeshQuality {
  filename: string;
  elements: number;
  nodes: number;
  totalVolume: number;
  stats: QualityStats;
  edgeRatioMax: number;
  poorCount: number;
  nonPositiveVolumeCount: number;
  nonFiniteCount: number;
  histogram: QualityBin[];
  worstElements: QualityElement[];
  poorThreshold: number;
  verdict: string;
}

function finiteNumber(value: unknown): number | null {
  // 刻意不用 `Number(value)`：它会接受 null / '' / true / [] 并给出 0，
  // 而 0 在这套数据里是一个**有意义的值**（质量可以是 0）。把"缺失"变成
  // "0" 会让界面显示一个看起来真实、实际是编造的结论。
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

function finiteNumberOr(value: unknown, fallback: number): number {
  const parsed = finiteNumber(value);
  return parsed === null ? fallback : parsed;
}

function nonNegativeInt(value: unknown): number {
  const parsed = finiteNumber(value);
  if (parsed === null) return 0;
  return Math.max(0, Math.trunc(parsed));
}

function parseBins(raw: unknown): QualityBin[] | null {
  if (!Array.isArray(raw)) return null;
  const bins: QualityBin[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== 'object') return null;
    const lo = finiteNumber((item as Record<string, unknown>).lo);
    const hi = finiteNumber((item as Record<string, unknown>).hi);
    const count = finiteNumber((item as Record<string, unknown>).count);
    if (lo === null || hi === null || count === null) return null;
    bins.push({ lo, hi, count });
  }
  return bins;
}

function parseElements(raw: unknown): QualityElement[] {
  if (!Array.isArray(raw)) return [];
  const items: QualityElement[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    const index = finiteNumber(record.index);
    const quality = finiteNumber(record.quality);
    if (index === null || quality === null) continue;
    items.push({
      index,
      quality,
      volume: finiteNumberOr(record.volume, 0),
      edgeRatio: finiteNumberOr(record.edgeRatio, 0),
    });
  }
  return items;
}

/**
 * 把后端响应归一化成可渲染的结构；不可用时返回 `null`。
 *
 * 返回 `null` 而不是"带默认值的对象"是刻意的：界面上宁可什么都不显示，
 * 也不要显示一排 0（那会被读成"质量是 0"或者"质量完美"）。
 */
export function toMeshQuality(raw: unknown): MeshQuality | null {
  if (raw === null || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;

  const stats = record.quality;
  if (stats === null || typeof stats !== 'object') return null;
  const statsRecord = stats as Record<string, unknown>;
  const min = finiteNumber(statsRecord.min);
  const max = finiteNumber(statsRecord.max);
  if (min === null || max === null) return null;

  const histogram = parseBins(record.histogram);
  if (histogram === null || histogram.length === 0) return null;

  const elements = nonNegativeInt(record.elements);
  if (elements <= 0) return null;

  return {
    filename: typeof record.filename === 'string' ? record.filename : '',
    elements,
    nodes: nonNegativeInt(record.nodes),
    totalVolume: finiteNumberOr(record.total_volume, 0),
    stats: {
      min,
      max,
      mean: finiteNumberOr(statsRecord.mean, min),
      median: finiteNumberOr(statsRecord.median, min),
      p05: finiteNumberOr(statsRecord.p05, min),
    },
    edgeRatioMax: finiteNumberOr(record.edge_ratio_max, 0),
    poorCount: nonNegativeInt(record.poor_count),
    nonPositiveVolumeCount: nonNegativeInt(record.non_positive_volume_count),
    nonFiniteCount: nonNegativeInt(record.non_finite_count),
    histogram,
    worstElements: parseElements(record.worst_elements),
    poorThreshold: finiteNumberOr(record.poor_threshold, 0.1),
    verdict: typeof record.verdict === 'string' ? record.verdict : '',
  };
}

/** 质量数值的显示（保留 4 位有效数字就够读）。缺失显示 `—`。 */
export function formatQuality(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return '—';
  return parsed.toFixed(4);
}

/**
 * 直方图某个箱的相对高度（0..1），用于画条形。
 *
 * `maxCount <= 0` 时返回 0——不做除法，否则空直方图会画出一片 `NaN` 高度的
 * 条形（浏览器里表现为整块空白，看不出是没数据还是坏了）。
 */
export function barHeight(count: unknown, maxCount: unknown): number {
  const value = finiteNumber(count);
  const ceiling = finiteNumber(maxCount);
  if (value === null || ceiling === null || ceiling <= 0) return 0;
  return Math.max(0, Math.min(1, value / ceiling));
}

/** 直方图里最大的箱计数（用于算条形高度）；空直方图返回 0。 */
export function maxBinCount(histogram: QualityBin[] | null | undefined): number {
  if (!Array.isArray(histogram) || histogram.length === 0) return 0;
  let best = 0;
  for (const bin of histogram) {
    const count = finiteNumber(bin && bin.count);
    if (count !== null && count > best) best = count;
  }
  return best;
}

/**
 * 一行摘要，例如 `min 0.2671 · 均值 0.7559 · 1571 单元`。
 *
 * **先给最小值**：判断网格能不能用看的是最差的那个单元，而不是平均值
 * （平均 0.75 的网格里完全可以藏着几个 0.02 的刀片单元）。
 */
export function describeQualitySummary(quality: MeshQuality | null): string {
  if (!quality) return '尚未检查';
  const parts = [`最低 ${formatQuality(quality.stats.min)}`];
  parts.push(`均值 ${formatQuality(quality.stats.mean)}`);
  parts.push(`${quality.elements} 单元`);
  return parts.join(' · ');
}

/**
 * 需要提醒用户的问题（没有问题时返回空数组）。
 *
 * 与后端 `verdict` 的分工：后端给的是**结论句**，这里给的是**分项数据**
 * （几个单元有问题、哪个最差）。两者都显示，因为"有 3 个单元质量低于 0.1"
 * 比"局部应力可能不可靠"更能让用户决定要不要加密网格。
 */
export function describeQualityWarnings(quality: MeshQuality | null): string[] {
  if (!quality) return [];
  const warnings: string[] = [];
  if (quality.nonPositiveVolumeCount > 0) {
    warnings.push(`${quality.nonPositiveVolumeCount} 个体积非正的单元（退化或朝向错误）`);
  }
  if (quality.nonFiniteCount > 0) {
    warnings.push(`${quality.nonFiniteCount} 个单元的质量无法计算`);
  }
  if (quality.poorCount > 0) {
    warnings.push(`${quality.poorCount} 个单元质量低于 ${formatQuality(quality.poorThreshold)}`);
  }
  if (warnings.length === 0) {
    warnings.push('未发现畸形单元');
  }
  return warnings;
}

/**
 * 直方图是否自洽：各箱计数之和必须等于单元数。
 *
 * 不自洽说明界面会"少画"一部分单元——而被丢掉的往往正是畸形单元，
 * 也就是最该被看到的那部分。所以这里把它显式暴露出来，而不是照画不误。
 */
export function histogramIsComplete(quality: MeshQuality | null): boolean {
  if (!quality) return false;
  let total = 0;
  for (const bin of quality.histogram) {
    const count = finiteNumber(bin && bin.count);
    if (count === null) return false;
    total += count;
  }
  return total === quality.elements;
}
