/**
 * 剖切面（clipping plane）的几何计算——纯函数。
 *
 * 为什么把几何抽出来单独成模块
 * --------------------------
 * "把零件切开看内部"这件事里，**只有几何是可以精确验证的**：
 * 平面方程、模型包围盒、以及在给定平面下有多少节点被剖掉，全是闭式可算的。
 * 而"剖开之后画面好不好看"没有任何判据（截图式的检查也证明不了数值对）。
 *
 * 所以这一层用 node 直接跑、断言精确数值（见 tools/tasks.py 的 verify 自检），
 * 三维渲染那一层只负责把算好的平面交给 three.js。**必须说清楚**：本模块的
 * 正确性有测试保证，"剖切在屏幕上确实生效"只有浏览器里能看到——我不假装
 * 验过后者。
 *
 * 平面约定（与 three.js 一致）
 * --------------------------
 * `Plane.distanceToPoint(p) = normal · p + constant`，**距离 >= 0 的一侧被保留**。
 * 本模块沿用同一形式，因此算出来的 `{normal, constant}` 可以原样喂给
 * `new THREE.Plane(...)`，中间不需要再换一套符号约定——符号约定换一次就够
 * 错一次了。
 *
 * 剖切方向
 * --------
 * `keepSide: 'below'` 保留坐标较小的一侧（`axis <= position`），
 * `'above'` 保留较大的一侧。`fraction` 是平面在包围盒里沿该轴的**相对位置**
 * （0 = 最小端，1 = 最大端）。
 */

export type ClipAxis = 'x' | 'y' | 'z';
export type KeepSide = 'below' | 'above';

export interface ClipSpec {
  enabled: boolean;
  axis: ClipAxis;
  /** 平面在包围盒内的相对位置，0..1；会被夹到范围内 */
  fraction: number;
  keepSide: KeepSide;
}

export interface PlaneEquation {
  normal: [number, number, number];
  constant: number;
}

export interface Bounds {
  min: [number, number, number];
  max: [number, number, number];
}

const AXIS_INDEX: Record<ClipAxis, number> = { x: 0, y: 1, z: 2 };

/** 默认状态：不剖切。界面从它开始，避免一进来就是一个切开的模型。 */
export const DEFAULT_CLIP_SPEC: ClipSpec = {
  enabled: false,
  axis: 'x',
  fraction: 0.5,
  keepSide: 'below',
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isVec3(value: unknown): value is number[] {
  return Array.isArray(value) && value.length >= 3 && value.every(isFiniteNumber);
}

/**
 * 模型包围盒（逐轴最小/最大值）。
 *
 * 节点里有非法值或没有节点时返回 `null`：**宁可让界面说"算不出来"，
 * 也不要拿一个编出来的包围盒去剖切**——那会把模型切在莫名其妙的位置上。
 */
export function modelBounds(nodes: ArrayLike<unknown> | null | undefined): Bounds | null {
  if (!nodes || nodes.length === 0) return null;
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (!isVec3(node)) return null;
    for (let axis = 0; axis < 3; axis += 1) {
      if (node[axis] < min[axis]) min[axis] = node[axis];
      if (node[axis] > max[axis]) max[axis] = node[axis];
    }
  }
  if (!min.every(isFiniteNumber) || !max.every(isFiniteNumber)) return null;
  return { min, max };
}

/** 平面沿该轴的坐标位置：`min + fraction * span`（fraction 夹到 [0, 1]）。 */
export function clipPosition(bounds: Bounds, axis: ClipAxis, fraction: unknown): number {
  const index = AXIS_INDEX[axis];
  const raw = isFiniteNumber(fraction) ? fraction : 0;
  const clamped = Math.min(1, Math.max(0, raw));
  return bounds.min[index] + clamped * (bounds.max[index] - bounds.min[index]);
}

/**
 * 平面方程。
 *
 * - 保留小的一侧：`normal = -轴单位向量`，`constant = position`
 *   ⇒ `distance = position - coord >= 0` 等价于 `coord <= position`；
 * - 保留大的一侧：`normal = +轴单位向量`，`constant = -position`。
 *
 * `enabled = false` 时给出一个**在包围盒之外**的平面（而不是"没有平面"）：
 * three.js 的着色器是按剖切面**数量**编译的，数量一变就得重新编译，滑杆每动
 * 一下就卡一下。恒定一个平面、只是把它挪到模型外面，就完全没有这个问题。
 */
export function planeEquation(spec: ClipSpec, bounds: Bounds): PlaneEquation {
  const index = AXIS_INDEX[spec.axis];
  const span = bounds.max[index] - bounds.min[index];
  // 退化情形（该轴跨度为 0，例如平面模型）：往外让开一个单位，保证"不剖切"
  // 时真的一个节点都不切
  const margin = span > 0 ? span : 1;

  const position = spec.enabled
    ? clipPosition(bounds, spec.axis, spec.fraction)
    : bounds.max[index] + margin;

  const normal: [number, number, number] = [0, 0, 0];
  if (spec.enabled && spec.keepSide === 'above') {
    normal[index] = 1;
    return { normal, constant: -position };
  }
  // 不剖切时也走这一支：把平面放到 max 之外，等价于保留全部
  normal[index] = -1;
  return { normal, constant: position };
}

/** 点到平面的有符号距离（>= 0 表示被保留）。 */
export function planeDistance(plane: PlaneEquation, point: unknown): number | null {
  if (!isVec3(point)) return null;
  return (
    plane.normal[0] * point[0]
    + plane.normal[1] * point[1]
    + plane.normal[2] * point[2]
    + plane.constant
  );
}

/**
 * 按平面给节点分类。
 *
 * 这是"剖切"这件事里唯一能给出**确切数字**的部分：剖掉了多少个节点。
 * 界面上把它显示出来（"剖掉 38% 的节点"），用户才知道自己看到的截面在哪儿，
 * 而不是只凭画面猜。
 */
export function classifyNodes(
  nodes: ArrayLike<unknown> | null | undefined,
  plane: PlaneEquation,
): { kept: number; removed: number; keptFraction: number } {
  if (!nodes || nodes.length === 0) return { kept: 0, removed: 0, keptFraction: 1 };
  let kept = 0;
  let total = 0;
  for (let index = 0; index < nodes.length; index += 1) {
    const distance = planeDistance(plane, nodes[index]);
    if (distance === null) continue;
    total += 1;
    if (distance >= 0) kept += 1;
  }
  return {
    kept,
    removed: total - kept,
    keptFraction: total > 0 ? kept / total : 1,
  };
}

/** 一行可直接显示的剖切说明（未启用时返回 `null`）。 */
export function describeClip(
  spec: ClipSpec,
  position: number,
  counts: { kept: number; removed: number; keptFraction: number },
): string | null {
  if (!spec.enabled) return null;
  const side = spec.keepSide === 'below' ? '较小' : '较大';
  const percent = (counts.keptFraction * 100).toFixed(1);
  return (
    `剖切 ${spec.axis} = ${position.toPrecision(4)}，保留${side}的一侧：`
    + `留下 ${counts.kept} 个节点、剖掉 ${counts.removed} 个（保留 ${percent}%）`
  );
}
