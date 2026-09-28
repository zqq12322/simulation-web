/**
 * 任务结果的读取，以及"结果被回收"的如实说明。
 *
 * 服务端按**体积**保留任务结果（见 `backend/jobs.py`）：单条结果过大时不保留
 * 内容，只留下 `resultDropped` 与 `resultBytes`。于是前端会拿到
 * `status === 'succeeded'` 且 `result === null` 这么一种组合。
 *
 * 为什么单独抽成纯函数并加自检：**说错话比不说更糟**。用户看到"求解完成"却是
 * 空结果，会以为是自己看错了、或者功能坏了；而如果这里把"没有结果"和"结果被
 * 回收"混为一谈，用户就会去反复重试同一件永远不会成功的事。
 */

/** 结果体积换算成 MB（四舍五入）。取不到或非法时返回 0。 */
export function jobResultMegabytes(raw: unknown): number {
  if (raw === null || typeof raw !== 'object') return 0;
  const bytes = (raw as Record<string, unknown>).resultBytes;
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes <= 0) {
    return 0;
  }
  return Math.round(bytes / (1024 * 1024));
}

/**
 * 结果是"被回收了"还是"本来就没有"。
 *
 * 判定条件是两个都要满足：`resultDropped === true` **且** `result == null`。
 * 只看 `resultDropped` 不够——将来如果有了"保留摘要但丢掉明细"的做法，
 * 那时 `result` 不为空，就不该再报"结果没保留"。
 */
export function resultWasDropped(raw: unknown): boolean {
  if (raw === null || typeof raw !== 'object') return false;
  const job = raw as Record<string, unknown>;
  return job.resultDropped === true && job.result === null;
}

/**
 * 结果被回收时给用户看的说明；不该报的时候返回 `null`。
 *
 * 文案必须给出**原因**和**下一步**：只说"没有结果"会让人反复重试。
 */
export function droppedResultMessage(raw: unknown): string | null {
  if (!resultWasDropped(raw)) return null;
  const megabytes = jobResultMegabytes(raw);
  const size = megabytes > 0 ? `约 ${megabytes} MB` : '';
  return (
    `求解已完成，但结果${size}超过服务端保留上限，未保留。`
    + '请减小网格规模（或降低模态阶数）后重试。'
  );
}
