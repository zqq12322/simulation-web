/**
 * 认证相关的**纯逻辑**：令牌存取、用户字段映射、请求头拼装、错误翻译。
 *
 * 为什么单独成模块
 * ----------------
 * 三件事都需要能被测试，而它们都容易写错：
 *
 * 1. **`localStorage` 会抛异常**。Safari 隐私模式、被策略禁用的存储、
 *    甚至某些企业环境里，访问 `localStorage` 本身就会抛 SecurityError。
 *    把它直接写在组件里，用户看到的是白屏。
 * 2. **令牌是"看起来很敏感"的东西**，最容易被不小心写进日志或 URL。
 * 3. **401 与"网络不通"必须区分**：前者要清掉令牌并回到登录页，
 *    后者只该提示"后端没起来"。混在一起会让用户反复重新登录却没用。
 *
 * 因此这里不依赖 React / axios，`tools/tasks.py verify` 会用 node **直接执行**它
 * 并断言行为（见 `docs/04` §8）。
 */

import type { Project } from '../types';

/** 令牌在 localStorage 里的键名。 */
export const TOKEN_STORAGE_KEY = 'simcloud.auth.token';

export interface AuthUser {
  id: string;
  username: string;
  displayName: string;
  createdAt: string;
}

export interface AuthSession {
  user: AuthUser;
  token: string;
}

/**
 * 最小存储接口。只依赖 `getItem`/`setItem`/`removeItem`，
 * 测试里可以传一个假实现（或直接测"存储抛异常"的路径）。
 */
export interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** 取浏览器的 localStorage；不可用时返回 null（而不是抛出去）。 */
export function defaultStorage(): TokenStorage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage;
  } catch {
    // 隐私模式/被禁用时，连访问都会抛
    return null;
  }
}

export function readStoredToken(storage: TokenStorage | null = defaultStorage()): string | null {
  if (!storage) return null;
  try {
    const token = storage.getItem(TOKEN_STORAGE_KEY);
    return typeof token === 'string' && token.trim() ? token : null;
  } catch {
    return null;
  }
}

export function writeStoredToken(
  token: string,
  storage: TokenStorage | null = defaultStorage(),
): boolean {
  if (!storage || !token) return false;
  try {
    storage.setItem(TOKEN_STORAGE_KEY, token);
    return true;
  } catch {
    // 存储写不进去（配额满/被禁用）不该让登录流程失败——本次会话仍然可用
    return false;
  }
}

export function clearStoredToken(storage: TokenStorage | null = defaultStorage()): void {
  if (!storage) return;
  try {
    storage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* 清不掉就算了，内存里的会话状态已经清空 */
  }
}

/** 后端用户记录 → 界面用的用户对象。缺 id/username 时返回 null。 */
export function toUser(record: unknown): AuthUser | null {
  const data = record as Record<string, unknown> | null;
  if (!data || typeof data !== 'object') return null;

  const id = typeof data.id === 'string' ? data.id : '';
  const username = typeof data.username === 'string' ? data.username : '';
  if (!id || !username) return null;

  return {
    id,
    username,
    displayName: typeof data.displayName === 'string' && data.displayName
      ? data.displayName
      : username,
    createdAt: typeof data.createdAt === 'string' ? data.createdAt : '',
  };
}

/** 登录/注册响应 → 会话对象。形状不对时返回 null，而不是带一个空令牌继续跑。 */
export function toSession(payload: unknown): AuthSession | null {
  const data = payload as Record<string, unknown> | null;
  if (!data || typeof data !== 'object') return null;

  const token = typeof data.token === 'string' ? data.token.trim() : '';
  const user = toUser(data.user);
  if (!token || !user) return null;

  return { user, token };
}

/**
 * 组装带认证的请求头。
 *
 * 没有令牌时**不**发一个 `Authorization: Bearer undefined`——那会让后端的
 * 错误信息变得莫名其妙。直接返回只含 Accept 的头，让后端回一个干净的 401。
 */
export function authorizationHeader(
  token: string | null | undefined,
  extra: Record<string, string> = {},
): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json', ...extra };
  const value = typeof token === 'string' ? token.trim() : '';
  if (value) headers.Authorization = `Bearer ${value}`;
  return headers;
}

/** 从 axios 错误里取 HTTP 状态码；取不到返回 null（网络层错误）。 */
export function errorStatus(error: unknown): number | null {
  const candidate = error as { response?: { status?: number } } | null;
  const status = candidate?.response?.status;
  return typeof status === 'number' ? status : null;
}

/** 该错误是否表示"登录已失效"（需要清令牌并回到登录页）。 */
export function isUnauthorized(error: unknown): boolean {
  return errorStatus(error) === 401;
}

/**
 * 会话失效事件名。
 *
 * 求解/网格/材料这些调用散落在 `Workbench`、`AIAssistantPanel`、`MaterialSelector`
 * 里，它们**拿不到 App 的会话状态**。与其把 token 与回调层层透传下去，
 * 不如让任何一处遇到 401 时广播一个事件，由 App 统一处理
 * （清令牌 + 回登录面板）。这与项目里既有的 `apply-ai-params` 事件是同一个套路。
 */
export const SESSION_EXPIRED_EVENT = 'simcloud:session-expired';

/**
 * 取当前令牌的请求头。
 *
 * 从存储里读，而不是要求每个调用方把 token 传进来：令牌本来就是这个 App 的
 * 会话级状态，存储已经是它的持久层——再传一份到各处只会制造两个真相。
 * 这样任何一处 axios 调用都能直接用，不必穿透组件树。
 */
export function currentAuthHeaders(
  extra: Record<string, string> = {},
): Record<string, string> {
  return authorizationHeader(readStoredToken(), extra);
}

/** 广播"会话已失效"，让 App 统一清理并回到登录面板。 */
export function notifySessionExpired(detail?: string): void {
  try {
    window.dispatchEvent(
      new CustomEvent(SESSION_EXPIRED_EVENT, { detail: detail || '' }),
    );
  } catch {
    // 非浏览器环境（例如 node 里跑断言）没有 window：忽略即可
  }
}

/**
 * 把认证请求的错误翻译成用户能看懂的一句话。
 *
 * **必须区分 401 与"连不上后端"**：前者要重新登录，后者要先把服务起起来。
 * 混为一谈会让用户反复重试一件永远不会成功的事。
 */
export function describeAuthError(error: unknown): string {
  const candidate = error as {
    response?: { status?: number; data?: { detail?: unknown } };
    code?: string;
    message?: string;
  } | null;

  const status = errorStatus(error);
  const detail = candidate?.response?.data?.detail;

  if (status === 401) {
    return typeof detail === 'string' && detail ? detail : '用户名或口令不正确。';
  }
  if (status === 403) {
    return typeof detail === 'string' && detail ? detail : '该操作不被允许。';
  }
  if (status === 422) {
    return typeof detail === 'string'
      ? `输入不合法：${detail}`
      : '输入不合法：请检查用户名（字母/数字/下划线，至少 3 位）与口令（至少 8 位）。';
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

/**
 * 项目卡片上的属主标记。
 *
 * 当前实现里列表只会返回自己的项目，因此这个函数主要用来**让归属显式可见**
 * （包括"无主"这种遗留状态），而不是做权限判断。
 */
export function describeOwner(
  project: Pick<Project, 'isPrivate'> & { ownerId?: string | null },
  currentUserId?: string | null,
): string {
  if (!project.ownerId) return '未归属';
  if (currentUserId && project.ownerId === currentUserId) {
    return project.isPrivate === false ? '我的项目（公开）' : '我的项目';
  }
  return '他人的项目';
}
