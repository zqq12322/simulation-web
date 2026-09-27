/**
 * 项目管理 API 的**纯逻辑**部分：把后端返回的记录映射成界面用的对象、
 * 以及把错误翻译成用户能看懂的话。
 *
 * 为什么不直接 `setProjects(response.data)`：后端的 `createdAt` 是 **ISO 字符串**，
 * 而 `types.ts` 里 `Project.createdAt` 声明为 `Date`。旧代码直接写
 * `proj.createdAt.toLocaleDateString()`，一旦数据来自 HTTP 就会抛
 * `TypeError: proj.createdAt.toLocaleDateString is not a function`
 * —— 这是"接上真实接口"时最容易漏掉的一类崩溃。映射放在这一个边界上，
 * 界面其余部分就可以放心地认为拿到的就是 `Date`。
 *
 * 这个模块不依赖 React / axios，因此 `tools/tasks.py verify` 可以用 node
 * **直接执行**它并断言行为（见 `docs/04` §8）。
 */

import type { Project } from '../types';

export type SimulationType = 'CFD' | 'FEA' | 'Thermal' | 'General';

/** 与后端 `projects.py` 的 `SIMULATION_TYPES` 保持一致。 */
export const SIMULATION_TYPES: SimulationType[] = ['CFD', 'FEA', 'Thermal', 'General'];

/** 后端 `/api/projects` 实际返回的记录形状（时间戳是字符串）。 */
export interface ProjectRecord {
  id?: unknown;
  title?: unknown;
  description?: unknown;
  simulationType?: unknown;
  isPrivate?: unknown;
  ownerId?: unknown;
  hasSetup?: unknown;
  createdAt?: unknown;
  updatedAt?: unknown;
}

function normaliseSimulationType(value: unknown): SimulationType {
  return SIMULATION_TYPES.includes(value as SimulationType)
    ? (value as SimulationType)
    : 'General';
}

/**
 * 解析时间戳。接受 `Date` 或 ISO 字符串；无法解析时返回 `null`
 * （让调用方显示占位符，而不是渲染出 `Invalid Date`）。
 */
export function parseTimestamp(value: unknown): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === 'string' || typeof value === 'number') {
    const parsed = new Date(value as string | number);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

/** 供界面显示的创建日期；缺失或非法时返回 `—`（不显示 `Invalid Date`）。 */
export function formatCreatedAt(value: unknown): string {
  const parsed = parseTimestamp(value);
  if (!parsed) return '—';
  try {
    return parsed.toLocaleDateString();
  } catch {
    // 极少数环境（无 Intl）下 toLocaleDateString 可能抛错
    return parsed.toISOString().slice(0, 10);
  }
}

/**
 * 单条记录 → `Project`。缺 `id` 或 `title` 时返回 `null`：
 * 渲染一张点不开、没名字的卡片比不显示更糟。
 */
export function toProject(record: ProjectRecord | null | undefined): Project | null {
  if (!record || typeof record !== 'object') return null;

  const id = typeof record.id === 'string' ? record.id.trim() : '';
  const title = typeof record.title === 'string' ? record.title : '';
  if (!id || !title) return null;

  return {
    id,
    title,
    description: typeof record.description === 'string' ? record.description : '',
    createdAt: parseTimestamp(record.createdAt) ?? new Date(0),
    simulationType: normaliseSimulationType(record.simulationType),
    isPrivate: record.isPrivate !== false,
    // 缺失与 null 都表示"无主"（后端用 owner_id IS NULL 表达遗留项目）。
    // 归一化成 null，界面就不必区分 undefined / null 两种情况。
    ownerId: typeof record.ownerId === 'string' && record.ownerId ? record.ownerId : null,
    // 列表接口只给"配过没有"的标记（完整配置走 /setup 子资源）
    hasSetup: record.hasSetup === true,
  };
}

/** 该项目是否无主（接上登录之前创建的遗留数据）。 */
export function isUnowned(project: Pick<Project, 'ownerId'> | null | undefined): boolean {
  return !project || !project.ownerId;
}

/**
 * 该项目能否被当前用户修改/删除。
 *
 * 无主项目**不能**——必须先认领。这样"看得见但要手点一下"取代了
 * "谁先注册谁自动得到"，后者曾把开发者手工建的项目静默划给测试账号。
 */
export function canModify(
  project: Pick<Project, 'ownerId'> | null | undefined,
  currentUserId: string | null | undefined,
): boolean {
  if (isUnowned(project)) return false;
  return !!currentUserId && project!.ownerId === currentUserId;
}

/** 列表映射：过滤掉坏记录，而不是让整个列表渲染失败。 */
export function toProjectList(records: unknown): Project[] {
  if (!Array.isArray(records)) return [];
  const projects: Project[] = [];
  for (const record of records) {
    const project = toProject(record as ProjectRecord);
    if (project) projects.push(project);
  }
  return projects;
}

/**
 * 把请求错误翻译成用户能看懂的一句话。
 *
 * 这是"界面不说谎"的一部分：后端没启动 / 项目已被删掉 / 请求体不合法，
 * 用户看到的原因必须不一样，而不是统一的 "Something went wrong"。
 */
export function describeProjectError(error: unknown): string {
  const candidate = error as {
    response?: { status?: number; data?: { detail?: unknown } };
    code?: string;
    message?: string;
  } | null;

  const status = candidate?.response?.status;
  const detail = candidate?.response?.data?.detail;

  if (status === 404) return '项目不存在（可能已被删除）。';
  if (status === 422) {
    return typeof detail === 'string'
      ? `请求不合法：${detail}`
      : '请求不合法：项目名称不能为空，且不能有多余字段。';
  }
  if (status === 400) {
    return typeof detail === 'string' ? detail : '请求被拒绝。';
  }
  if (status && status >= 500) return `后端出错（HTTP ${status}）。`;

  const code = candidate?.code;
  if (code === 'ERR_NETWORK' || code === 'ECONNREFUSED') {
    return '无法连接后端：请确认它已启动（python tools/tasks.py dev）。';
  }
  if (typeof candidate?.message === 'string' && candidate.message) {
    return candidate.message;
  }
  return '未知错误。';
}
