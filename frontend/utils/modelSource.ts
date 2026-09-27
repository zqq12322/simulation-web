/**
 * 几何文件的取用方式（带认证）。
 *
 * 背景：为什么不再直接用 `/uploads/xxx.stl`
 * ----------------------------------------
 * 原先几何预览是 `${API_BASE}/uploads/${文件名}` 通过**公开的静态目录**提供的：
 * 知道文件名就能下载，不需要登录。而 `uploads/` 里既有用户上传的 CAD 原件，
 * 也有派生的预览 STL —— 项目数据是按属主隔离的，几何文件却能被任何人拿走，
 * 两条承诺互相矛盾。
 *
 * 之所以当初没法简单加鉴权：three.js 的 `STLLoader`/`GLTFLoader` **不会带
 * `Authorization` 头**。所以现在的做法是：
 *
 *    带认证头 fetch → Blob → URL.createObjectURL → 把 blob: URL 交给加载器
 *
 * 加载器拿到的就是一个普通的、不需要认证的 URL，而真正的请求已经带了令牌。
 *
 * 这个模块把"取文件"这件事做成可注入依赖的纯逻辑（`fetchImpl` /
 * `createObjectURL` 都能替换），因此 `tools/tasks.py verify` 可以用 node
 * 完整断言成功与各种失败路径，而不需要浏览器。
 *
 * **认证头由调用方传入，这个模块刻意不自己去读令牌存储**：它不该知道令牌放在
 * 哪里（将来换成 HttpOnly Cookie 只用改调用方），也让它可以被 node 单独执行
 * ——跨模块的相对导入在 Node 的 ESM 解析器下需要显式扩展名，而 TS/Vite 允许省略，
 * 两边规则不同。保持"无内部依赖"能避免踩这个差异。
 */

/** 带状态的加载错误，便于上层区分"没登录"与"文件没了"。 */
export class ModelLoadError extends Error {
  status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'ModelLoadError';
    this.status = status;
  }
}

/** 认证下载地址（真正被请求的 URL）。 */
export function geometryDownloadUrl(apiBase: string, filename: string): string {
  const base = String(apiBase || '').replace(/\/+$/, '');
  return `${base}/api/geometry/${encodeURIComponent(filename)}/download`;
}

/**
 * 上传时后端为 STEP/IGES 生成的预览文件名（`<原名>.stl`），
 * 见 `backend/geometry.py` 的 `upload_geometry_impl`。
 *
 * 独立成函数是因为它必须与后端**规则一致**：不一致会让"重新打开项目"
 * 永远取不到预览（而这类错误只会表现为"视口是空的"，很难查）。
 */
export function renderFilenameFor(geometryFilename: string): string {
  const name = String(geometryFilename || '');
  if (/\.(step|stp|iges|igs)$/i.test(name)) return `${name}.stl`;
  return name;
}

/** 文件名是否可用于请求（与后端 `resolve_upload_path` 同样的直观规则）。 */
export function isSafeFilename(filename: string): boolean {
  const name = String(filename || '');
  if (!name || name === '.' || name === '..') return false;
  if (name.includes('/') || name.includes('\\') || name.includes(':')) return false;
  if (name.includes('\x00')) return false;
  return /\.(stl|step|stp|iges|igs|msh|gltf|glb)$/i.test(name);
}

/**
 * 把 HTTP 失败翻译成用户能看懂的一句话。
 *
 * **401 与 404 必须分开**：前者要重新登录，后者是"这个文件不在了"
 * （可能被清理过）。混在一起会让用户对着一个永远不会成功的操作反复重试。
 */
export function describeModelError(status: number | null, filename: string): string {
  if (status === 401) return '登录已失效，请重新登录后再加载几何。';
  if (status === 404) return `几何文件不存在：${filename}（可能已被清理，请重新导入）。`;
  if (status === 400) return `几何文件名不合法：${filename}`;
  if (status === null) return '无法连接后端：请确认它已启动。';
  return `加载几何失败（HTTP ${status}）：${filename}`;
}

export interface ModelBlobResult {
  /** 交给 three.js 加载器的 URL */
  url: string;
  /** 用完必须调用，否则每个模型都会留下一块无法回收的内存 */
  revoke: () => void;
}

export interface FetchModelOptions {
  /**
   * 认证头（**必填**）。设为必填是为了让"忘记带令牌"在**编译期**就被发现——
   * 否则它只会在运行时变成一个"登录已失效"的 401，与真正的登录失效混在一起。
   */
  headers: Record<string, string>;
  /** 便于测试注入；默认用全局 fetch */
  fetchImpl?: typeof fetch;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
}

/**
 * 带认证取回几何文件，返回可直接交给加载器的 `blob:` URL。
 *
 * 失败时抛 `ModelLoadError`（带 `status`），消息已经是可以直接显示的文案。
 */
export async function fetchModelBlobUrl(
  apiBase: string,
  filename: string,
  options: FetchModelOptions,
): Promise<ModelBlobResult> {
  // 先做客户端校验：把明显非法的名字挡在请求之前，
  // 也避免把 `../` 之类的东西拼进 URL。
  if (!isSafeFilename(filename)) {
    throw new ModelLoadError(`几何文件名不合法：${filename}`, 400);
  }

  const doFetch = options.fetchImpl
    ?? (typeof fetch !== 'undefined' ? fetch : undefined);
  if (!doFetch) {
    throw new ModelLoadError('当前环境不支持 fetch，无法加载几何。', null);
  }

  const headers = { ...(options.headers || {}) };

  let response: Response;
  try {
    response = await doFetch(geometryDownloadUrl(apiBase, filename), { headers });
  } catch {
    // 网络层失败（后端没起、跨域被拒）：与 HTTP 状态码区分开
    throw new ModelLoadError(describeModelError(null, filename), null);
  }

  if (!response.ok) {
    throw new ModelLoadError(describeModelError(response.status, filename), response.status);
  }

  const blob = await response.blob();
  const create = options.createObjectURL
    ?? (typeof URL !== 'undefined' && URL.createObjectURL
      ? URL.createObjectURL.bind(URL)
      : null);
  if (!create) {
    throw new ModelLoadError('当前环境不支持 Blob URL，无法加载几何。', null);
  }
  const revokeImpl = options.revokeObjectURL
    ?? (typeof URL !== 'undefined' && URL.revokeObjectURL
      ? URL.revokeObjectURL.bind(URL)
      : () => { /* 没有 revoke 就只丢给 GC */ });

  const url = create(blob);
  return { url, revoke: () => revokeImpl(url) };
}
