/**
 * 模态结果的展示逻辑（纯函数，不依赖 React / three.js）。
 *
 * 这个模块存在的理由和 `deformation.ts` 一样：把"能出错的判断"从组件里搬出来，
 * 这样 `tools/tasks.py verify` 可以用 node **直接执行**它并断言行为，
 * 而不必在 Python 里重写一遍（重写一遍等于测试了一个副本）。
 *
 * 关于振型的两个物理约定（界面必须如实表达）：
 *
 * 1. **振型的幅值是任意的**。特征值问题 `K φ = λ M φ` 的解只确定方向，幅值可以
 *    任意缩放，因此后端把每个振型按"最大节点位移 = 1"归一化后再返回。
 *    所以振型的云图是**无量纲的相对量**，不是米/毫米——图例必须写明，
 *    否则用户会把颜色读成真实位移。
 * 2. **刚体模态的频率恰好是 0**（自由-自由结构有 6 个：3 平移 + 3 转动）。
 *    它们不是"算错了"，而是结构没被充分约束时的正常结果，界面上要标明。
 */

export interface ModeEntry {
  /** 0 起的模态序号，用于回传给父组件 */
  index: number;
  /** 1 起的阶次，用于显示 */
  order: number;
  /** 固有频率，Hz */
  frequency: number;
  /** 显示用的频率文案 */
  label: string;
  /** 是否为刚体模态（频率≈0） */
  isRigidBody: boolean;
}

/** 模态云图的图例文案。振型已归一化，因此是相对量。 */
export const MODE_LEGEND_TITLE = '相对位移（振型）';
export const MODE_LEGEND_UNIT = '(归一化, 无量纲)';

/** 无法显示时的占位符。不要用 "0 Hz" 冒充——那会把"缺失"说成"零频"。 */
export const UNKNOWN_FREQUENCY_LABEL = '—';

/**
 * 把固有频率格式化成便于阅读的文案。
 *
 * 分档是为了让同一张列表里的量级差异可读：一个 10 mm 钢块的前几阶在
 * 几十 kHz，而同模型的刚体模态是 0 Hz，直接用 Hz 全写成 `55747.3 Hz` 很难扫。
 *
 * 注意这里**必须在 Number() 之前**排掉 null / undefined / '' / 布尔值：
 * `Number(null)`、`Number('')`、`Number(false)` 全都等于 0，会被当成
 * "频率为 0 的刚体模态"显示出去——把"数据缺失"说成"零频"是彻底的误导。
 * （本函数的单元断言正是抓到了这个：`formatFrequency(null)` 曾返回 '0 Hz'。）
 *
 * 入参类型是 `unknown` 而不是 `number`：这些值来自后端 JSON，到了这里就必须
 * 按"不可信输入"处理，而不是相信类型声明。
 */
export function formatFrequency(hz: unknown): string {
  if (hz === null || hz === undefined || hz === '' || typeof hz === 'boolean') {
    return UNKNOWN_FREQUENCY_LABEL;
  }

  const value = Number(hz);
  if (!Number.isFinite(value) || value < 0) return UNKNOWN_FREQUENCY_LABEL;
  if (value < 1e-6) return '0 Hz';
  if (value >= 1e6) return `${(value / 1e6).toFixed(3)} MHz`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(2)} kHz`;
  return `${value.toFixed(2)} Hz`;
}

/**
 * 第一阶**弹性**模态的频率（Hz）；取不到时返回 `null`。
 *
 * 为什么不直接取 `frequencies[0]`：自由-自由结构的前若干阶是**刚体模态**
 * （频率恒为 0，且与网格无关）。拿第 0 阶去记录或比较，会得到一个永远为 0
 * 的"结果"——看着像算出来了，其实什么都没测。
 *
 * 这里与 `buildModeList` 共用同一份"哪些是刚体模态"的规则（都是
 * `index < rigidBodyModes`），所以界面上的阶次标注与记录下来的频率不会打架。
 */
export function firstElasticFrequency(
  frequencies: ArrayLike<number> | null | undefined,
  rigidBodyModes = 0,
): number | null {
  if (!frequencies || frequencies.length === 0) return null;
  const rigidCount = Math.max(0, Math.floor(Number(rigidBodyModes)) || 0);
  const index = Math.min(rigidCount, frequencies.length - 1);
  const value = Number(frequencies[index]);
  return Number.isFinite(value) ? value : null;
}

/**
 * 生成模态列表。`rigidBodyModes` 来自后端（`rigid_body_modes`），
 * 表示前多少阶是频率≈0 的刚体模态。
 */
export function buildModeList(
  frequencies: ArrayLike<number> | null | undefined,
  rigidBodyModes = 0,
): ModeEntry[] {
  if (!frequencies || frequencies.length === 0) return [];
  const rigidCount = Math.max(0, Math.floor(Number(rigidBodyModes)) || 0);

  const entries: ModeEntry[] = [];
  for (let index = 0; index < frequencies.length; index += 1) {
    const frequency = Number(frequencies[index]);
    entries.push({
      index,
      order: index + 1,
      frequency: Number.isFinite(frequency) ? frequency : Number.NaN,
      label: formatFrequency(frequency),
      isRigidBody: index < rigidCount,
    });
  }
  return entries;
}

/** 把用户点选的序号夹到合法范围内；没有模态时返回 -1。 */
export function clampModeIndex(index: number, modeCount: number): number {
  const count = Math.max(0, Math.floor(Number(modeCount)) || 0);
  if (count === 0) return -1;
  const value = Math.floor(Number(index));
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value, 0), count - 1);
}

export interface ModeDisplayField {
  /** 传给顶点着色器的位移场（与 `nodes` 一一对应） */
  displacements: number[][];
  /** 用于着色的标量场：每个节点的振型位移模长（相对量） */
  scalarField: number[];
  /**
   * 最大位移模长。后端把振型归一化到 1，但这里**从实际数据算**而不是假定为 1
   * ——万一归一化方式改了，放大系数会自动跟着变，而不是悄悄失真。
   */
  maxDisplacement: number;
}

/**
 * 取出第 `index` 阶振型，转成显示所需的三个场。
 *
 * 数据不匹配时返回 `null` 而不是"尽量凑一个"：振型长度与节点数对不上时，
 * 画出来的几何是错的，而错的云图比不显示更糟（用户会当真）。
 */
export function modeDisplayField(
  modeShapes: ArrayLike<number[][]> | null | undefined,
  index: number,
  nodeCount: number,
): ModeDisplayField | null {
  const count = Math.floor(Number(nodeCount)) || 0;
  if (count <= 0) return null;
  if (!modeShapes || modeShapes.length === 0) return null;

  const resolved = clampModeIndex(index, modeShapes.length);
  if (resolved < 0) return null;

  const shape = modeShapes[resolved];
  if (!Array.isArray(shape) || shape.length !== count) return null;

  const displacements: number[][] = new Array(count);
  const scalarField: number[] = new Array(count);
  let maxDisplacement = 0;

  for (let node = 0; node < count; node += 1) {
    const vector = shape[node] || [0, 0, 0];
    const x = Number(vector[0]) || 0;
    const y = Number(vector[1]) || 0;
    const z = Number(vector[2]) || 0;
    displacements[node] = [x, y, z];
    const magnitude = Math.sqrt(x * x + y * y + z * z);
    scalarField[node] = magnitude;
    if (magnitude > maxDisplacement) maxDisplacement = magnitude;
  }

  return { displacements, scalarField, maxDisplacement };
}

/**
 * 当前振型是否"看起来不对"时的提示文案（返回 `null` 表示没问题）。
 *
 * 这里刻意不静默：刚体模态频率为 0 是**正确**结果，但用户不知道的话会以为
 * 求解器坏了或者模型有问题，所以要说清"这是为什么、怎么办"。
 */
export function modeHint(
  entry: ModeEntry | null | undefined,
  rigidBodyModes: number,
): string | null {
  if (!entry) return null;
  if (entry.isRigidBody) {
    return '刚体模态：结构未被充分约束，整体可以自由平移/转动，因此频率为 0。'
      + '要得到弹性模态，请添加固定约束。';
  }
  if (rigidBodyModes > 0) {
    return `前 ${rigidBodyModes} 阶为刚体模态（频率 0），当前显示的是第 ${entry.order} 阶弹性模态。`;
  }
  return null;
}
