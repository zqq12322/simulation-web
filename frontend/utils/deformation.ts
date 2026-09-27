/**
 * 变形显示的纯计算部分（不依赖 three.js，因此可以直接用 node 跑断言）。
 *
 * 为什么需要"放大系数"
 * -------------------
 * 钢材在常规载荷下的位移是微米~毫米量级，而 CAD 零件是几十~几百毫米。
 * 按 1:1 画出来等于看不见，所以云图必须把变形放大。但放大倍数**不能写死**：
 *
 * - 旧代码写的是 `targetVisualDisp = 0.5`，直接当成几何坐标里的 0.5 个单位。
 *   对一个 100 mm 的零件，最大位移被放大到 0.5 mm —— 仍然是看不见；
 *   对一个 1 单位大小的模型，却是模型尺寸的 50% —— 夸张到失真。
 *   同一个常量在小零件上太小、在大零件上太大，说明它本来就该是**相对量**。
 *
 * 现在的做法：把最大位移放大到**模型最大尺度的固定比例**（默认 8%）。
 * 这样无论模型多大、单位是 m 还是 mm，画面上的变形量都一致可比。
 *
 * 这个模块被 `tools/tasks.py verify` 用 node 直接执行并断言
 * （node >= 23 可以import .ts），因此它既是实现也是被验证的对象。
 */

/** 变形显示的目标幅度：最大位移放大到模型最大尺度的这个比例。 */
export const TARGET_DEFORMATION_FRACTION = 0.08;

/** 放大系数的下限：避免浮点噪声被放大（位移≈0 时不应该抖动）。 */
export const MIN_DEFORMATION_SCALE = 1.0;

/** 放大系数的上限：防止位移极小（或接近刚体模态）时系数爆炸。 */
export const MAX_DEFORMATION_SCALE = 1e6;

/**
 * 模型的最大尺度（三个方向尺寸的最大值）。
 *
 * 与后端 `fe_utils.compute_model_span` 的定义保持一致：选面/施加载荷的相对
 * 容差也用它，因此前端显示与后端判定用的是同一个"尺寸感"。
 */
export function modelSpanOf(nodes: ArrayLike<number[]> | undefined | null): number {
  if (!nodes || nodes.length === 0) return 0;

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

  for (let i = 0; i < nodes.length; i += 1) {
    const node = nodes[i];
    if (!node) continue;
    const [x, y, z] = node;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    if (z > maxZ) maxZ = z;
  }

  const span = Math.max(maxX - minX, maxY - minY, maxZ - minZ);
  return Number.isFinite(span) && span > 0 ? span : 0;
}

/**
 * 计算变形放大系数。
 *
 * ``scale = 目标比例 × 模型尺度 / 最大位移``
 *
 * 边界情形都返回 ``1``（即"按真实比例显示"）而不是 ``Infinity``/``NaN``：
 * 位移为 0 或数据缺失时，画面应该静止，而不是把浮点噪声放大到屏幕上。
 */
export function computeDeformationScale(
  maxDisplacement: number | undefined | null,
  modelSpan: number | undefined | null,
  targetFraction: number = TARGET_DEFORMATION_FRACTION,
): number {
  const displacement = Number(maxDisplacement);
  const span = Number(modelSpan);

  if (!Number.isFinite(displacement) || displacement <= 0) return 1;
  if (!Number.isFinite(span) || span <= 0) return 1;
  if (!Number.isFinite(targetFraction) || targetFraction <= 0) return 1;

  const scale = (targetFraction * span) / displacement;
  if (!Number.isFinite(scale) || scale <= 0) return 1;

  return Math.min(Math.max(scale, MIN_DEFORMATION_SCALE), MAX_DEFORMATION_SCALE);
}

/**
 * 把后端返回的 ``[[ux,uy,uz], ...]`` 摊平成着色器要的 ``vec3`` 属性数组。
 *
 * 长度必须与几何顶点数**严格一致**：多了会被 WebGL 忽略，少了顶点属性的
 * 默认值在规范里是未定义的（多数实现给 ``(0,0,0,1)``，但不能依赖它）。
 * 因此这里总是返回恰好 ``3 × nodeCount`` 个元素，缺失部分补 0。
 */
export function flattenDisplacements(
  displacements: ArrayLike<number[]> | undefined | null,
  nodeCount: number,
): Float32Array {
  const count = Math.max(0, Math.floor(nodeCount) || 0);
  const flat = new Float32Array(count * 3);
  if (!displacements) return flat;

  const available = Math.min(displacements.length, count);
  for (let i = 0; i < available; i += 1) {
    const vector = displacements[i];
    if (!vector) continue;
    flat[i * 3] = Number(vector[0]) || 0;
    flat[i * 3 + 1] = Number(vector[1]) || 0;
    flat[i * 3 + 2] = Number(vector[2]) || 0;
  }
  return flat;
}

/** 每个节点的位移模长，用于按位移大小着色（模态分析的振型尤其需要）。 */
export function displacementMagnitudes(
  displacements: ArrayLike<number[]> | undefined | null,
): number[] {
  if (!displacements) return [];
  const magnitudes: number[] = [];
  for (let i = 0; i < displacements.length; i += 1) {
    const vector = displacements[i] || [0, 0, 0];
    const x = Number(vector[0]) || 0;
    const y = Number(vector[1]) || 0;
    const z = Number(vector[2]) || 0;
    magnitudes.push(Math.sqrt(x * x + y * y + z * z));
  }
  return magnitudes;
}

/** 判断结果里是否带有可显示的位移场。 */
export function hasDisplacementField(
  displacements: unknown,
  nodeCount: number,
): boolean {
  return (
    Array.isArray(displacements)
    && displacements.length > 0
    && nodeCount > 0
    && displacements.length === nodeCount
  );
}
