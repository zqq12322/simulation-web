# SimCloud AI · 智仿云（云端 AI 辅助仿真平台）

一个 **Web 端 CAE（计算机辅助工程）仿真平台**：导入 3D 几何 → 划分网格 → 选材料 → 加边界条件 → 有限元求解 → 3D 应力云图。
前后端分离，内置 AI 助手（DeepSeek）辅助配置参数与诊断报错。

> 定位：教学 / 大创项目原型。求解结果为**定性参考**，尚不可用于工程定案（见文末「已知限制」）。

---

## 1. 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19 + TypeScript + Vite 6 + three.js / @react-three/fiber + Tailwind（CDN） |
| 后端 | Python 3.12 + FastAPI + Uvicorn |
| 几何/网格 | Gmsh 4.15（内置 OpenCASCADE 内核），支持 STEP / IGES / STL |
| 求解器 | scikit-fem 12：**线弹性静力** + **稳态热传导** + **模态分析**（线性四面体单元） |
| AI | DeepSeek（OpenAI 兼容接口） |
| 存储 | 本地 `uploads/` 目录；SQLite（标准库）存自定义材料与**项目**；Supabase Storage 可选（未配置时自动禁用） |
| 部署 | Vercel（`vercel.json`：Python Serverless + 静态前端） |

## 2. 目录结构

```
大创项目/
├── backend/                    # FastAPI 后端
│   ├── main.py                 # 应用入口，挂载 5 个路由 + 静态 uploads/
│   ├── geometry.py             # 几何上传/STEP 转 STL/B-Rep 元数据提取/网格生成
│   ├── solver.py               # 线弹性静力求解（scikit-fem）
│   ├── materials.py            # 材料库（内存，5 种）
│   ├── constraints.py          # 边界条件数据模型 + 设置校验
│   ├── ai_assistant.py         # DeepSeek 助手：chat / diagnose / configure
│   ├── supabase_client.py      # 可选云存储
│   ├── config.py               # ★ 集中配置：绝对路径、上传/网格限制、CORS、单位制
│   ├── jobs.py                 # ★ 后台任务：单线程工作器（gmsh 非线程安全）+ /api/jobs/*
│   ├── fe_utils.py             # ★ 共享 FE 基础设施：读网格、归属面积、面→节点定位
│   ├── thermal.py              # 稳态热传导求解（第二个分析类型）
│   ├── modal.py                # ★ 模态分析：K φ = λ M φ（第三个分析类型）
│   ├── gmsh_session.py         # Gmsh 会话（主线程初始化一次，进程内复用）
│   ├── material_store.py       # 材料库持久化（SQLite）
│   ├── project_store.py        # ★ 项目持久化（SQLite）：ID/时间戳由服务端生成
│   ├── sqlite_store.py         # ★ SQLite 存储基类：连接/事务/**关闭** + 幂等加列迁移
│   ├── projects.py             # ★ 项目管理 API（/api/projects 增删改查）
│   ├── generate_step.py        # 生成测试件（方块挖通孔）
│   ├── generate_stl.py
│   ├── tests/                  # unittest 回归测试（含物理校准，无需启动服务器）
│   ├── requirements.txt        # 直接依赖（版本已锁定）
│   ├── requirements.lock.txt   # 完整依赖树（用于完全复现环境）
│   ├── .env.example            # 复制为 .env 后填写密钥
│   ├── uploads/                # 上传的几何与网格缓存（*.msh）
│   └── venv/                   # Python 虚拟环境（唯一在用的那个）
├── frontend/                   # 前端（Vite 项目根，原目录名 "2026 1 24" 来自 AI Studio 导出）
│   ├── index.html              # 入口 HTML（CDN 加载 Tailwind / Font Awesome）
│   ├── index.tsx               # React 挂载点
│   ├── App.tsx                 # 落地页 → 项目仪表盘 → 工作台 的路由状态机
│   ├── types.ts                # 全局类型契约
│   ├── vite-env.d.ts           # import.meta.env 类型声明
│   ├── components/             # 11 个组件（见下）
│   │   ├── resultShader.ts     # 结果云图着色器：彩虹色映射 + **真实变形显示**
│   │   └── ...
│   ├── utils/
│   │   ├── deformation.ts      # 变形放大系数与顶点属性（纯函数，可被 node 直接测试）
│   │   ├── modalModes.ts       # 模态阶次列表 / 频率格式化 / 振型取场（同上）
│   │   └── projectsApi.ts      # 项目记录的接口↔界面映射、错误翻译（同上）
│   └── dist/                   # 生产构建产物
├── docs/                       # 项目文档（先读这里）
│   ├── 01-开发流程与长期计划.md
│   ├── 02-学习路线.md
│   ├── 03-修复记录.md
│   └── 04-如何扩展求解器.md
├── tools/
│   └── tasks.py                # ★ 跨平台任务入口：setup / dev / test / verify / build / clean / doctor
├── scripts/                    # Windows 薄封装（转发到 tools/tasks.py，不另写实现）
│   ├── _python.ps1             # 解释器探测（避开 Microsoft Store 的 python 别名）
│   ├── setup.ps1 / dev.ps1 / test.ps1 / verify.ps1
├── Dockerfile                  # 后端镜像（含 Gmsh 所需系统库）
├── docker-compose.yml          # 一键起前后端（开发用）
├── Makefile                    # make setup / dev / test / verify / build / clean
├── .github/workflows/ci.yml    # CI：push / PR 自动跑测试与构建
├── .github/PULL_REQUEST_TEMPLATE.md  # PR 模板：把"三层验证"固化成必答项
├── .github/ISSUE_TEMPLATE/     # 缺陷/功能建议表单（都要求给出解析解或验收判据）
├── CONTRIBUTING.md             # 协作指南（改代码前先读）
├── .gitattributes              # 统一换行符，避免协作时出现整文件 diff
├── .editorconfig               # 编辑器一致性（含 .ps1 需 UTF-8 BOM 的说明）
├── vercel.json                 # 部署配置
└── .gitignore
```

前端核心组件：

| 组件 | 职责 |
|---|---|
| `Workbench.tsx` | 工作台总调度：仿真树 + 状态管理 + 调用后端 |
| `Scene3D.tsx` | three.js 视口：点/边/面拾取、约束与载荷可视化、结果云图着色器（含**变形显示**）、图例与支反力面板 |
| `ImportModal.tsx` | 几何上传（拖拽，STL/STEP/IGES） |
| `MaterialSelector.tsx` | 材料选择 + 自定义材料 |
| `BoundaryConditionSelector.tsx` | 5 种边界条件的增删改 |
| `MeshSettingsModal.tsx` | 网格类型/尺寸/质量/局部细化 |
| `SolverSettingsModal.tsx` | 分析类型与求解参数 |
| `AIAssistantPanel.tsx` | AI 聊天，可解析 JSON 配置并一键套用 |
| `LandingPage.tsx` | 营销首页 |
| `NewProjectModal.tsx` | 新建项目 |

## 3. 快速开始

**环境要求**：Python 3.9+（推荐 3.12）、Node.js 20+。Windows / Linux / macOS 通用。

```bash
# 首次安装（创建 venv、装依赖、生成 .env）
make setup              # 或 python3 tools/tasks.py setup

# 启动前后端（Ctrl+C 一起停）
make dev                # 或 python3 tools/tasks.py dev
```

Windows 也可以用原来的写法（薄封装，转发到同一个实现）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

访问：**前端 http://localhost:3000** ｜ 后端 http://127.0.0.1:8000 （API 文档 http://127.0.0.1:8000/docs ）

**完全不想配本机环境**：

```bash
docker compose up --build
```

> ⚠️ `Dockerfile` / `docker-compose.yml` **尚未在真实 Docker 上验证过**（开发机未装 Docker）；
> YAML 语法已校验，但首次 `docker build` 若报缺库，请补 `Dockerfile` 的 apt 清单。

手动启动（不使用任务脚本时）：

```bash
# 后端
cd backend
./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000   # Windows: .\venv\Scripts\python.exe

# 前端
cd frontend && npm run dev
```

### 用一个例子跑通全流程

1. 打开 http://localhost:3000 → 「立即开始仿真」→ **注册一个账号**（第一个账号就是你的）；
2. 新建项目进入工作台。若列表里有标着"未归属"的项目（接上登录之前创建的），点「认领」即可；
3. 导入几何（`backend/uploads/test_part.step` 是内置的"方块挖通孔"测试件）；
4. 左侧 `Mesh` → ⚙ → **保存并生成网格**（会真实调用 Gmsh）；
5. `Model → Materials` → 选材料；`Boundary conditions` → 加一个固定约束 + 一个力载荷（在 3D 视口点面即可选中）；
6. `Simulation Runs` → ▶ 求解 → 视口切换为 Von Mises 应力云图 + 图例 + 支反力。

## 4. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 健康检查 |
| GET | `/api/health` | 健康检查（含上传目录绝对路径，供 CI/容器探针使用） |
| **POST** | **`/api/auth/register`** | **注册并直接登录**（201；首个账号即你自己）。可用 `SIMCLOUD_ALLOW_REGISTRATION=0` 关闭自助注册 |
| **POST** | **`/api/auth/login`** | 换一个登录令牌（口令错误与用户不存在返回**同一句** 401） |
| **POST** | **`/api/auth/logout`** | 登出（幂等；删掉当前会话） |
| **GET** | **`/api/auth/me`** | 当前登录用户（前端启动时用它校验本地令牌是否还有效） |
| **GET** | **`/api/auth/config`** | 公开的认证配置（是否允许注册、长度限制） |
| GET | `/api/materials` | 材料列表 |
| GET | `/api/materials/{id}` | 单个材料 |
| POST | `/api/materials` | 新增自定义材料（SQLite 持久化，重启不丢） |
| **GET** | **`/api/projects`** | **项目列表**（只返回自己的 + 标记为"未归属"的遗留项目；**需登录**） |
| **POST** | **`/api/projects`** | **新建项目**（201；ID/时间戳/属主由服务端决定，请求体里带 `id` 会 422） |
| **GET** | **`/api/projects/{id}`** | 单个项目（不存在**或不属于自己**都返回 404） |
| **PATCH** | **`/api/projects/{id}`** | 改名/改描述/改类型/改可见性（一个字段都没给则 400） |
| **POST** | **`/api/projects/{id}/claim`** | **认领无主项目**（幂等；不能认领别人的） |
| **DELETE** | **`/api/projects/{id}`** | 删除自己的项目（不存在或不属于自己返回 404） |
| **GET** | **`/api/project-metadata`** | 可选分析类型与字段长度上限（供前端渲染，避免两处硬编码） |
| POST | `/api/generate-cube` | 生成 10×10×10 演示立方体 STEP |
| POST | `/api/upload-geometry` | 上传几何；STEP/IGES 自动转 STL 供网页预览 |
| GET | `/api/geometry/{filename}/metadata` | 提取 B-Rep 面/边/顶点（类型、面积、中心、法线） |
| POST | `/api/generate-mesh` | 生成四面体网格，返回节点/单元/面/边/顶点 |
| POST | `/api/solve` | 线弹性静力求解（同步；大模型请用 `/api/jobs/solve`） |
| POST | `/api/thermal/solve` | **稳态热传导**求解（同步；大模型请用 `/api/jobs/thermal`） |
| POST | `/api/modal/solve` | **模态分析**求解（同步；大模型请用 `/api/jobs/modal`） |
| POST | `/api/jobs/generate-mesh` | **异步**划分网格：立即返回 `job_id`（202） |
| POST | `/api/jobs/solve` | **异步**结构求解：立即返回 `job_id`（202） |
| POST | `/api/jobs/thermal` | **异步**热传导求解：立即返回 `job_id`（202） |
| POST | `/api/jobs/modal` | **异步**模态分析：立即返回 `job_id`（202） |
| GET | `/api/jobs/{job_id}` | 查询任务状态与结果（`queued`/`running`/`succeeded`/`failed`） |
| GET | `/api/jobs` | 列出最近的任务与队列状态 |
| POST | `/api/validate-setup` | 求解前校验（材料、约束、载荷是否齐全） |
| POST | `/api/ai/chat` | AI 问答 |
| POST | `/api/ai/diagnose` | AI 诊断仿真设置/报错 |
| POST | `/api/ai/configure` | 自然语言 → 结构化仿真参数 JSON |

## 5. 当前状态

已验证通过：

- `tsc --noEmit` 无错误；`vite build` 成功；
- **`python3 tools/tasks.py test`：249 个后端用例全部通过**（约 8 秒，无需启动服务器）；
- **`python3 tools/tasks.py verify`：56 项端到端检查全部通过**；
- 全流程跑通：上传 → 网格 → 求解 → 云图（含变形显示）；
- 求解器物理正确性抽查：10×10×10 立方体轴向拉伸，加载面中心位移 `4.17e-10` vs 解析解 `FL/AE = 5e-10`（比值 0.835，全约束端略刚于自由杆，符合预期）；支反力合计与施加载荷精确抵消。

### 数值验证现状（每一项都对着解析解或守恒律）

| 物理量 | 对照量 | 实测偏差 |
|---|---|---|
| 支反力守恒 | 合力 = −载荷 | −1000.000 N vs +1000 N |
| 圆柱面面积 | `2πrh` | 188.50 vs 188.50（几何） |
| 立方体轴向刚度 | `FL/AE` | 比值 0.953（全约束端略刚） |
| Von Mises 应力 | 解析场 `2με` | < 1e-6（发现过 1e7 倍错误） |
| 面载荷合力 | `p·A` | 1e9（精确） |
| 面载荷分配 | `FacetBasis` 精确积分 | 相对差 3.7e-16 |
| 强制位移 | 指定位移 / 线性翻倍 / `EAδ/L` | 误差 0 / 精确 / 比值 1.041 |
| 单位制 | 应力 ×1000²、位移 ×1000 | < 1e-6 |
| 稳态热传导 | 温度线性 / `q = kΔT/L` | < 1e-9 K / < 1e-6 |
| **模态·刚体不变量** | `Ku = 0`、`uᵀMu` = `ρV` 与 `ρV(w²+h²)/12` | **1e-16**（机器精度） |
| **模态·自由-自由** | 恰好 6 个零特征值 | 零模态/λ₇ ≈ 4e-12 |
| **模态·侧限杆轴向频率** | `(2n−1)/(4L)·√((λ+2μ)/ρ)` | 二阶收敛：6.4e-3 → 4e-5（nx=4→32） |
| **模态·单位缩放** | ω ∝ 1/L ⇒ mm/m 频率比 1000 | 1000.000000000008 |

> 注意第 10 行：`uᵀMu = ρV` 校验的是**一致质量矩阵**本身，与特征值求解器无关；
> 第 12 行的参考模量是**侧限模量** `λ+2μ` 而不是 `E`——这两者的差别（ν=0.3 时 16%）
> 曾经被误当成"求解器有 16% 误差"，详见 `docs/03-修复记录.md`。

### 已知限制

| 项 | 现状 | 影响 |
|---|---|---|
| 载荷施加 | 已按**归属面积加权**（节点力之和 = 载荷 × 面积）；`pressure` 也已真正实现 | 曲面上的压力仍需面法向可用，否则会给出警告并忽略 |
| 边界条件选点 | 已从 `.msh` **精确映射**几何实体 → 边界三角形；仅在无映射时才回退到几何搜索 | 由旧版本生成的 `.msh` 可能缺少实体信息 |
| `displacement` 边界条件 | ✅ 已实现（逐分量 `fixedX/Y/Z` + 非零值，走 skfem 非齐次 Dirichlet） | — |
| `temperature` 边界条件 | 未被结构求解器实现（会出现在 `warnings` 中提示）；热分析属阶段 2/4 | 需要时需新增分析类型 |
| 分析类型 | ✅ 线弹性静力 + 稳态热传导 + **模态分析**，三种都**后端已实现且前端已接线**；Fluid Flow (CFD) 未实现，选中时会**明确拒绝**而不是悄悄按结构分析求解 | 无屈曲 / 接触 / 非线性 |
| 求解/上传端点的访问控制 | ✅ **已完成**：求解/热/模态/几何/网格/材料/AI 八个 router 全部要求登录（路由级依赖），未登录一律 401 | — |
| `/uploads/` 静态文件 | ⚠️ **仍开放**：几何预览文件（`.stl`）通过静态目录直接提供，不校验登录 | 知道文件名即可取到上传的几何。要收紧得改用签名 URL 或给 three.js 的加载器补认证头——已在 `docs/01` 登记 |
| 项目共享 | 只有"私有/公开"标记，没有真正的共享（"Shared with me" 是占位） | 无法把项目交给别人协作 |
| 材料库 | ✅ 内置材料在代码里，自定义材料持久化到 SQLite（重启不丢） | 无编辑/删除界面（API 层已支持删除） |
| ~~单位制~~ | ✅ 已支持 `m` / `mm`：前端可选，后端换算成米再求解，结果一律 SI（位移 m、应力 Pa） | 换单位后物理结果一致（应力 ×1000²、位移 ×1000） |
| 几何拾取 | 面标签靠包围盒中心近似 | 复杂件上标签可能错位 |
| 结果后处理 | 整体云图 + **真实变形显示**（放大系数按模型尺度自动取，图例标注倍数）+ **模态振型选择**（阶次面板，标注刚体模态） | 无剖切/等值面/动画/报告导出 |
| 项目管理 | ✅ **已持久化 + 已按属主隔离**：项目存在后端 SQLite，跨刷新与重启都在；每个账号只看到并只能修改自己的项目 | 无共享/协作编辑（公开项目目前只是标记，还不能真的共享） |
| 用户与登录 | ✅ 用户表 + 口令哈希（scrypt）+ 服务端会话令牌；接口与界面都要求登录 | 求解/上传类端点**仍未要求登录**（见下方"已知限制"） |
| 长任务 | ✅ 已提供异步任务接口 `/api/jobs/*`（提交→轮询），前端已改用；任务在**单线程**工作器里排队（gmsh 非线程安全） | 任务表在进程内，**服务重启即丢**（生产级需外部队列） |
| 前端 i18n | 中英文混杂 | 体验不统一 |
| LandingPage | 部分按钮为占位链接 | 无实际功能 |

完整的问题清单与修复过程见 `docs/03-修复记录.md`。

## 6. 安全提醒

- **DeepSeek API Key 曾硬编码在源码中（已泄露），请到平台轮换。** 现在从 `backend/.env` 读取（该文件已被 `.gitignore` 忽略），并且 `backend/tests/test_validation.py` 里有一道自动化闸门会扫描源码中的 `sk-` 字面量，防止它再回来。
- 后端 CORS 默认 `*`，但已可用 `CORS_ALLOW_ORIGINS` 环境变量收紧 —— **生产环境务必设置**。
- **路径穿越风险已修复**：所有用户可控的文件名都经过 `config.resolve_upload_path()` 校验（拒绝路径分隔符、`..`、盘符；扩展名白名单；解析后必须落在 `uploads/` 内），上传另有 50 MB 上限（`MAX_UPLOAD_BYTES` 可调）。对应测试见 `backend/tests/test_config.py`。

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/01-开发流程与长期计划.md` | 如何用 DeepSeek Harness 迭代这个项目 + 分阶段路线图 |
| `docs/02-学习路线.md` | 补哪些领域的知识、学到什么程度、对应本项目哪块代码 |
| `docs/03-修复记录.md` | 修复记录：每一项问题的根因、修复与验证方式（可直接用于项目报告） |
| `docs/04-如何扩展求解器.md` | **要加功能先读这篇**：求解器数据流、新增边界条件/分析类型的步骤、验证判据表、已知陷阱 |
| `CONTRIBUTING.md` | 协作指南：环境、验证命令、红线、提交规范、如何新增功能、常见问题 |
| `tools/tasks.py` | 跨平台任务入口：`setup` / `dev [--detach]` / `stop` / `test` / `verify` / `build` / `clean` / `doctor` |

## 8. 整理记录

### 阶段 3 · 计算类端点的访问控制（本轮新增）

> 上一轮只保护了项目端点。`/api/solve`、`/api/upload-geometry`、`/api/generate-mesh`
> 等**仍对所有人开放**：共享部署下任何人都能占算力、传文件、花掉 DeepSeek 的额度。
> 项目数据是隔离的，但**计算资源不是**。

| 项 | 落地内容 | 意义 |
|---|---|---|
| 路由级依赖 | 八个 router（solver/thermal/modal/geometry/jobs/materials/constraints/ai）改为 `APIRouter(dependencies=[Depends(require_user)])` | 一句覆盖全部端点；以后新增端点自动受保护，不会漏 |
| 为什么不用逐端点参数 | 这些端点**不需要知道"你是谁"**（不像项目端点要按属主过滤）；更实际的是，40 多个既有测试是**直接调用端点函数**的（不经 HTTP），逐个加必填参数会让它们全部失效——而那层测试正是物理正确性的防线。路由级依赖只在 HTTP 层生效 | 249 个用例全绿 |
| 前端令牌获取 | 新增 `currentAuthHeaders()`：从存储读令牌并组装请求头 | **不把 token 穿过组件树**：它本来就是这个 App 的会话级状态，存储已经是持久层，再传一份只会制造两个真相 |
| 401 与"算不出来"分开 | 网格/求解/AI 失败原本会触发"AI 诊断"；登录失效时那会把用户引向完全无关的方向。现在 401 一律 `notifySessionExpired()` 广播事件，由 App 统一清令牌 + 回登录面板 | 与项目里既有的 `apply-ai-params` 事件同一个套路，避免把回调层层透传 |
| multipart 上传 | 上传 FormData 时**不显式设** `Content-Type: multipart/form-data` | boundary 必须由浏览器自己生成，手写反而可能让后端解析不出分片 |

**verify 新增 4 项**：逐个确认"未登录访问"会被拒——`/api/materials`、几何元数据、
`/api/solve`、`/api/jobs`。它们以前是开放的，所以必须显式钉住。
另外把 verify 的顺序调整为"**先认证、再跑其余检查**"——认证现在是所有检查的前置条件。

实测的带令牌 / 不带令牌对照：

```
GET  /api/materials                     无令牌=401  带令牌=200
GET  /api/geometry/.../metadata         无令牌=401  带令牌=200
GET  /api/jobs                          无令牌=401  带令牌=200
POST /api/upload-geometry（multipart）  无令牌=401  带令牌=200
```

### 阶段 3 · 登录与用户隔离

> 上一轮项目已持久化，但**没有"谁拥有哪个项目"**：同一个后端上所有人共用一份列表，
> 任何人都能改名/删除别人的项目。这一轮把用户与归属补上——**这是"可协作"与
> "互相看不见"的分界线**。

| 项 | 落地内容 | 意义 |
|---|---|---|
| 口令存储 `passwords.py` | 标准库 `hashlib.scrypt`（内存硬，n=2¹⁴/r=8/p=1）+ 每用户独立随机盐；`secrets.compare_digest` 常数时间比较 | "怎么存口令"是一旦写错事后无法补救的决定，所以它是一段短小、可独立测试的代码 |
| 参数写进哈希串 | 存成 `scrypt$n$r$p$salt$hash`，登录成功时顺手重算升级 | 若把 n/r/p 留在代码常量里，**改一次参数就等于让所有老用户登不上** |
| 会话 `auth_store.py` | 不透明随机令牌 + SQLite 会话表，**库里只存令牌的哈希** | 数据库被读走也拿不到可直接使用的令牌；可吊销（登出/改密只是删一行） |
| 为什么不用 JWT | 可吊销；不必自己实现密码学（手工拼 HMAC/base64url 并校验声明是经典高危动作）；过期判断写在 SQL 的 `WHERE` 里，不会因为某处忘记判断而放行过期会话 | 代价是每个请求多一次索引查询，对 SQLite 可忽略 |
| 不给用户名枚举 | "用户不存在"与"口令错误"耗时接近（陪跑一次假哈希），返回**同一句** 401 | 区分开就等于免费提供一个用户名枚举接口 |
| 项目隔离 | `owner_id` 走 `SqliteStore.migrations` 补列；存储层的 `owner_id` 是**必填参数**，没有"不加过滤返回全部"这个模式 | 若把 `None` 解释成"不过滤"，任何一处忘记传参的调用都会把所有用户的数据混在一起 |
| 404 而不是 403 | 别人的项目一律 404 | 403 等于确认"这个 id 存在，只是不是你的"，可被用来探测别人有哪些项目 |
| 前端 | 真实登录/注册（`AuthPanel`）、令牌持久化、启动时向 `/auth/me` 校验、401 自动回登录页、侧边栏显示真实用户并可登出 | 之前 `isLoggedIn` 只是一个客户端布尔量，点一下就算"登录"了 |

**🔴 本轮最值得记的一件事：我自己引入、并靠检查真实数据发现的缺陷**

初版写的是"第一个注册的用户自动接管所有无主项目"，本意是让遗留数据不至于"消失"。
结果 `tools/tasks.py verify` 注册的固定测试账号成了第一个用户，把开发者手工在浏览器里
建的项目**静默划给了测试账号**——用户下次登录就会发现项目不见了。
（是靠打印真实库内容发现的：项目数比预期多一个。）

修正为**可见 + 显式认领**：无主项目对所有已登录用户可见（标"未归属"）、
**不可改不可删**、可以一键认领。**静默改变数据归属，比"看得见但要手点一下"危险得多。**
已受影响的数据已还原。详见 `docs/03-修复记录.md` 第十五轮。

### 阶段 3 · 项目持久化

> 在此之前后端**根本没有"项目"这个实体**：仪表盘上的项目列表是 `App.tsx` 里
> 一个硬编码数组（`{ id: '1', title: 'Aerodynamic Wing v3' }`），新建项目只存在
> 浏览器内存里。刷新就丢，重启更是全丢，**也无法被引用或共享**——而"可协作"
> 的前提是存在一个可以被共享的对象。

| 项 | 落地内容 | 意义 |
|---|---|---|
| `project_store.py` | SQLite 表 `projects`：id / 标题 / 描述 / 分析类型 / 是否私有 / 创建与更新时间 | 项目成为一等持久化实体 |
| `sqlite_store.py` | 抽出 SQLite 存储基类：连接、事务、**显式关闭**、幂等加列迁移；`MaterialStore` 改为继承 | `_cursor` 那段编码了一个真实 bug（`with sqlite3.connect()` 不关闭连接 ⇒ 句柄泄漏并锁库）。这类规则写两遍一定会漂移 |
| `/api/projects` CRUD | 增删改查 + `GET /api/project-metadata` | 前端不再依赖 mock |
| **ID 由服务端生成** | 请求体里带 `id` 直接 422，而不是静默忽略 | 客户端能自选主键就能覆盖别人的记录；静默忽略则会让用户以为按自己的 ID 存下了 |
| **排序按 `rowid`** | 不按 `created_at`（只精确到秒，同一秒内顺序不确定） | 否则"最新在前"会时好时坏——典型的 flaky 测试来源 |
| 前端接线 | `App.tsx` 改为挂载时拉取、新建走 POST、卡片可删除；补上加载中 / 失败可重试 / 空列表三种状态 | 后端不可用时**说出来**，而不是显示空列表让用户以为项目丢了 |
| **修掉一个接入即崩的 bug** | 旧代码 `proj.createdAt.toLocaleDateString()`：后端返回的是 **ISO 字符串**，字符串没有这个方法 | 时间戳解析集中到 `utils/projectsApi.ts` 一个边界上，界面其余部分可以放心用 `Date` |
| 未实现的意图 | `owner_id` 与鉴权**故意没加** | 一个"存在但没人校验"的属主字段比没有更危险：它会让人以为数据已经隔离了。真正接登录时再加列，`SqliteStore.migrations` 就是为这种演进准备的 |

**验证**（104 个新用例里的 28 个 + `verify` 新增 8 项）

| 层次 | 断言 | 实测 |
|---|---|---|
| 存储 | 换 store 实例（模拟重启）数据仍在 | ✅ |
| 存储 | 同一秒内连建 5 个项目，顺序严格"最新在前" | ✅ |
| 存储 | 项目库与材料库**共用同一个 SQLite 文件**且互不干扰 | ✅ |
| HTTP | 201 / 404 / 400 / 422 各自的触发条件 | ✅ |
| HTTP | 请求体带 `id` 或空标题 → 422 | ✅ |
| **端到端** | **建项目 → 停后端 → 起后端 → 按 ID 取回** | ✅ 标题、描述、类型、可见性完全一致；中文与 emoji 原样往返；PATCH 也正常 |
| 前端 | node 直接执行 `projectsApi.ts`：映射、时间戳解析、错误翻译 | ✅ 含"字符串没有 `toLocaleDateString`"这条崩溃回归断言 |

### 阶段 3 · 模态分析前端接线

> 上一轮只做了后端，`SolverSettingsModal` 里只能如实标注"模态已实现但界面未接线"。
> 本轮把它接上——界面上不再有"已实现却点不了"的功能。

| 项 | 落地内容 | 意义 |
|---|---|---|
| 分析类型分流 | `Workbench.handleSolve` 按 `solverType` 选端点：`thermal` → `/api/jobs/thermal`，`modal` → `/api/jobs/modal`，其余 → `/api/jobs/solve` | 三种已实现的分析类型都真正可执行 |
| **拒绝未实现的类型** | 选 Fluid Flow (CFD) 时**明确报错并中止**，而不是按结构分析去算 | 此前 CFD 会被当成结构静力求解——用户拿到的结果与所选分析类型完全无关，是典型的"界面在说谎" |
| 模态结果默认显示第 1 阶 | 求解完成后取 `mode_shapes[0]` 作为位移场，用它的位移模长着色 | 振型本质上就是一个位移场，因此**完全复用**上一轮做好的变形显示管线，只换语义与标签 |
| 阶次选择面板 | 视口内列出全部固有频率，点击切换振型；刚体模态（频率 0）标注"刚体"并给出文字解释 | 模态分析的核心交互；不解释的话用户会以为"频率 0"是求解器坏了 |
| **单位/语义不撒谎** | 图例标题改为"相对位移（振型）"、单位"（归一化, 无量纲）" | 振型的幅值是任意的（后端按最大位移归一化），**不是米/毫米**。标成位移单位会让用户把颜色读成真实变形量 |
| 支反力面板 | 仅在结构分析显示（模态同样没有支反力） | 与热分析一致 |
| 验证 | `verify` 新增 3 项：模态纯函数断言（阶次列表/频率格式化/振型取场）、以及**把后端真实返回的模态结果喂给前端取场逻辑**的两项契约检查 | 后者专门挡"字段名漂移"：`mode_shapes` 一旦改名，界面只会静默地不显示振型，不报错也不影响数值 |

### 阶段 2 收尾 · 模态分析 + 变形显示

> 本轮补上了路线图「阶段 2」最后一项带解析解验收标准的能力，同时修掉两个
> **看代码看不出来、看图才发现**的显示层缺陷。三个提交分别对应下面三块。

**① 模态分析（第三个分析类型）**

| 项 | 落地内容 | 意义 |
|---|---|---|
| 特征值问题 | 新增 `backend/modal.py` + `POST /api/modal/solve` + `POST /api/jobs/modal`：装配 `K` 与**一致质量矩阵** `M = ρ∫N_i·N_j dV`，解 `K φ = λ M φ` | 固有频率与振型是结构设计的基本量；也证明这套架构能长出"完全不同的数学形式"，不只是一种线性解方程 |
| 质量矩阵 | `skfem.models.poisson.mass` 写的是 `u*v`，**对 `ElementVectorH1` 不做分量缩并**，直接抛 broadcast 错误；必须自己用 `dot(u,v)` 定义 | 这类 API 细节只会在运行时报错，静态检查发现不了 |
| 数值稳定性 | 自由-自由结构的 `K` **本来就是奇异的**（刚体模态就是它的零空间），`sigma=0` 的 shift-invert 不可靠。改用负 shift `-1e-6·trace(K)/trace(M)`，使分解对象 `K+αM` 恒正定 | 实测大 shift（0.1·trace 比值）会**丢掉一个零模态**——不是保守，是必要 |
| 可复现性 | ARPACK 默认用随机初始向量 ⇒ 同一输入结果不稳定；改为固定 `v0` | 不可复现的结果等于没有测试 |
| 物理语义 | 载荷不影响固有频率 ⇒ `force`/`pressure`/`temperature` 被忽略并**说明原因**；非零"指定位移"在齐次特征值问题里没有意义，也明确提示 | 避免"我加了 1000 N，频率怎么没变"变成反复出现的疑问 |
| 验证 | 34 个用例，见上表第 10–13 行：刚体不变量到机器精度、自由-自由恰好 6 个零频、侧限杆轴向频率**二阶收敛**、单位缩放恰好 1000 倍 | — |

**② 结果云图显示真实变形（缺陷修复）**

| 项 | 根因 | 修复 |
|---|---|---|
| 云图不显示变形 | 顶点着色器里 `deformationScale` **声明了但从没被使用**，`deformedPosition` 恒等于 `position`。不报错、不影响任何数值，只在"求解后看图"时表现为"看不出变形" | 着色器抽到 `frontend/components/resultShader.ts`，真正计算 `position + displacement * deformationScale`，并新增 `displacement` 顶点属性 |
| 放大系数写死 | 旧代码 `targetVisualDisp = 0.5` 是**绝对坐标量**：对 100 mm 的零件相当于把位移放大到 0.5 mm（仍看不见），对 1 单位模型却是尺寸的 50%（夸张失真） | 改为相对量：放大到模型最大尺度的 8%（`frontend/utils/deformation.ts`），图例标注实际倍数 |
| 机器检查 | — | `verify` 新增两项：①着色器契约（位移属性必须参与顶点计算，且 `deformedPosition` 不许等于 `position`）；②用 **node 直接执行前端的 `.ts`** 断言放大系数的跨尺寸/跨量级一致性与退化输入 |

**③ 协作基建**

| 项 | 落地内容 | 意义 |
|---|---|---|
| PR 模板 | `.github/PULL_REQUEST_TEMPLATE.md`：把「静态/接口/物理」三层验证做成**必答勾选项**，改了数值逻辑必须填解析解对照表 | "改完要真跑"这条纪律靠在文档里劝是没用的，得让它出现在每次 PR 的必经之路上 |
| Issue 表单 | `.github/ISSUE_TEMPLATE/`：缺陷报告要求给出**参考值/解析解**；功能建议要求给出**验收判据**（用什么算例证明它是对的） | 仿真项目的 bug 报告里"结果看起来不对"几乎没有信息量 |
| 修复记录 | 本轮记录 + 三条"我的检查/测试写错了，不是代码错了"的复盘（侧限模量、单位缩放、跨尺寸一致性） | 错误结论也要留证据，否则下一个人会重新踩 |

### 已完成（第一轮整理）

| 项 | 处理 | 根因 |
|---|---|---|
| `frontend/vite.config.ts` | 删除 `define` 中的 Gemini Key 注入，及连带的 `loadEnv` 调用与导入 | AI Studio 模板遗留。**全仓库无任何代码消费 `process.env.API_KEY`**——它唯一的作用是把密钥字符串打进前端产物，既无用又扩大泄密面 |
| `frontend/.env.local` | 删除 | 内容只有 `GEMINI_API_KEY=PLACEHOLDER_API_KEY`，其唯一消费者随上一条移除；`scripts/setup.ps1` 会从 `.env.example` 重建为正确的 `VITE_API_URL` 配置 |
| `frontend/README.md` | 重写为指向根 README 的简短说明 | 原文件是 AI Studio 模板说明（`npm install` + 设 `GEMINI_API_KEY` + ai.studio 链接），与当前实现（DeepSeek、根目录文档）矛盾，会误导接手的人 |
| 前端目录名 | `2026 1 24/` → `frontend/` | 原名是 AI Studio 导出时的日期，不可读**且含空格**——空格路径在脚本与命令行里到处需要引号，是踩坑源。已同步更新 `vercel.json`（2 处）、`scripts/*.ps1`（硬编码路径）与全部文档引用 |
| `backend/uploads/*.msh` | 脱离 git 跟踪（`.gitignore` 新增 `backend/uploads/*.msh`） | 这些是 `POST /api/generate-mesh` **每次都会重建的可再生缓存**，属二进制、单文件可达 200KB。继续跟踪会持续产生无意义 diff 并让仓库膨胀。**仅从索引移除，磁盘文件保留** |
| `backend/__pycache__/` | 清理 Python 3.10 的残留字节码 | 项目已迁移到 Python 3.12，旧标签的 `.pyc` 永远不会被加载，纯磁盘噪音 |

### 阶段 2 · 物理正确性（本轮新增）

> 本轮最有价值的产出不是新功能，而是**发现并修掉了一个把 Von Mises 应力放大 7 个数量级的错误**——详情见 `docs/03-修复记录.md` 第五节。

| 项 | 落地内容 | 意义 |
|---|---|---|
| 🔴 修复应力后处理 | 偏应力误写成 `trace(s)·eye(trace(s),3)`（等于把迹平方），改为 `s - (1/3)·eye(trace(s),3)` | 受压工况下应力曾偏大 1e7 倍；修复后带孔方块峰值应力 4169 → **47.97 Pa**（名义 10 Pa，符合孔边应力集中量级） |
| 新增解析解防线 | `VonMisesAnalyticTest`：用解析位移场 `u_x = εx`（Von Mises = `2με`）以 1e-6 相对容差比对 | 早先只断言"应力有限、无 NaN"，所以漏掉了上述错误 |
| 实现 `pressure` | 补齐模型字段 + `traction = -p·n` 真实面载荷 | 此前压力被**静默忽略**，用户拿到的是零载荷结果 |
| 精确面定位 | 从 `.msh` 读取 Gmsh 的实体→边界三角形映射，替代几何猜测；载荷按归属面积分配 | 不再依赖启发式；`FL/AE` 比值由 0.835 改善到 **0.953** |
| 警告可见化 | `SolverResult.warnings` + 前端黄色横幅 | 杜绝"求解成功但结果其实是错的" |
| 契约加固 | `BoundaryCondition` 开启 `extra="forbid"` 并补齐前端用到的全部字段 | 字段对不上会 422，而不是静默丢值（`pressure` 就是这么丢的） |
| 日志 | `logging_config.py` 统一日志，替换 16 处 `print` | 可分级、可关闭、能定位模块 |

### 阶段 1 · 工程化地基（上一轮新增）

让项目「可协作、能长期推进」的最低要求：别人能一键装好、改完能验证、不再被历史坑绊倒。

| 项 | 落地内容 | 解决的问题 |
|---|---|---|
| 集中配置 | 新增 `backend/config.py`：`UPLOAD_DIR` 改为**基于 `__file__` 的绝对路径**；CORS、上传上限、网格尺寸范围全部可用环境变量覆盖 | 此前 `UPLOAD_DIR = "uploads"` 是相对路径，**必须 `cd backend` 才能启动**，否则几何文件会写到别处 |
| 输入校验 | `resolve_upload_path()`：拒绝 `/` `\` `..` 与盘符、扩展名白名单、解析后必须落在 `uploads/` 内；上传加 50 MB 上限；`validate_mesh_size()` 限制尺寸范围 | 路径穿越风险；非法参数直接送进 Gmsh |
| 错误码修正 | 三个几何端点 + 求解端点补 `except HTTPException: raise` | 此前 `raise HTTPException(400)` 会被外层 `except Exception` 吞掉并**改写成 500**，前端拿不到真实原因 |
| 回归测试 | 新增 `backend/tests/`（**45 个用例，0.33 秒**）：配置安全边界、几何解析值（圆柱面 2πrh）、**求解器物理校准（FL/AE + 支反力守恒）**、材料与校验规则、密钥卫生 | 以前「改完只能靠肉眼看结果对不对」 |
| 一键测试 | 新增 `scripts/test.ps1`（**不需要启动服务器**，CI 可直接复用） | 降低验证门槛 |
| 依赖锁定 | `requirements.txt` 锁定直接依赖版本；新增 `requirements.lock.txt`（72 个包的完整依赖树） | 换机器装出来的行为不一致 |
| 换行符统一 | 新增 `.gitattributes`（`* text=auto eol=lf` + 二进制声明） | Windows/Linux 协作时的「整文件改动」噪音 diff |
| 编辑器一致性 | 新增 `.editorconfig`（含 `.ps1` **必须 UTF-8 BOM** 的说明） | 中文 PowerShell 脚本存成无 BOM 就解析失败（本项目踩过） |
| 协作指南 | 新增 `CONTRIBUTING.md`：验证命令、红线、提交规范、新增功能/测试模板、常见问题 | 新人不知道「改完要跑什么、什么不能提交」 |
| CI | 新增 `.github/workflows/ci.yml`：push/PR 自动跑后端测试 + 前端类型检查与构建 | 靠人记得跑测试不可靠 |

### 阶段 2 续 · 热传导前端接线（本轮新增）

| 项 | 落地内容 | 意义 |
|---|---|---|
| 分析类型分流 | `Workbench` 按 `solverType` 选择端点：`thermal` → `POST /api/jobs/thermal`，其余 → `/api/jobs/solve` | 上一轮只做了后端，选了 Heat Transfer 仍走结构求解——属于"界面在说谎" |
| 单位换算 | 前端 UI 用**摄氏度**、后端 API 用**开尔文**：提交时 `°C + 273.15`，显示时 `K − 273.15` | 避免 25 °C 被当成 25 K 这类静默错误 |
| 结果可视化 | `Scene3D` 新增 `resultKind`：热分析用**温度云图**（图例标注 °C），结构分析仍是 Von Mises（Pa）；支反力面板仅结构显示 | 复用同一套标量场着色管线，只换语义与单位标签 |
| 前置校验 | 热分析没有温度边界条件时直接提示"未指定的面按绝热处理；全绝热则问题无解"，而不是等后端报 400 | 把 400 变成可操作的引导 |
| 验证 | `verify` 新增一项**前端契约检查**：完全按 UI 的实际请求形状（°C→K、`length_unit=mm`、异步轮询）跑一遍，断言 `q = kΔT/L = 5e5 W/m²` | 无法点击浏览器，就把"UI 发出的请求"本身拿去端到端验证 |

### 阶段 2 续 · 稳态热传导

| 项 | 落地内容 | 意义 |
|---|---|---|
| 第二个分析类型 | 新增 `backend/thermal.py` + `POST /api/thermal/solve` + `POST /api/jobs/thermal`：稳态热传导 `∇·(k∇T)=0`，面给定温度（Dirichlet），**未指定的面天然绝热**（弱形式的自然边界条件） | 证明这套架构能长出新的物理场，而不是只能改一处 |
| 材料库扩展 | `Material` 增加 `thermalConductivity`（W/(m·K)），5 个内置材料都补了手册值（铜 401 > 铝 167 > 钢 50 > 钛 22 > ABS 0.2）；**SQLite 表新增列并带自动迁移**，老数据库打开时补列且旧数据不丢 | 顺带把"schema 演进"这件事做了一遍——长期项目一定会遇到 |
| 共享 FE 基础设施 | 新增 `backend/fe_utils.py`：网格读取、归属面积、**面→节点定位**（结构与传热共用同一套规则，改一次两边生效） | 消除"两个求解器各写一套"的漂移风险 |
| 验证 | 立方体两端定温（0/100 °C）、其余面绝热 ⇒ **温度沿轴向精确线性、热流 = kΔT/L**（解析解逐点校验，误差 < 1e-9 / < 1e-6）；单位一致性（mm 下热流 ×1000、温度不变）；缺温度边界 → 400；材料缺 k → 400；缺网格 → 409 | 线性单元能精确重现线性解（patch test），所以这里的容差可以卡到机器精度 |

### 阶段 3 · 长任务异步化

| 项 | 落地内容 | 意义 |
|---|---|---|
| 异步任务接口 | 新增 `backend/jobs.py`：`POST /api/jobs/generate-mesh`、`POST /api/jobs/solve` 立即返回 `job_id`，`GET /api/jobs/{job_id}` 轮询状态与结果；另有 `GET /api/jobs` 看队列 | 此前网格/求解同步阻塞 HTTP，**大模型必然超时**（用户拿到 502，而任务其实还在算） |
| 单线程工作器 | 所有 gmsh 操作（**包括原同步端点**）都排进同一个单线程执行器 | gmsh 是进程级全局状态、**非线程安全**；串行执行既消除并发破坏，也让事件循环保持响应 |
| 前端 | `Workbench` 改为「提交 → 每 700ms 轮询」，并在 Job status 里显示"排队中…/计算中…" | 大模型只是"变慢"，不再超时；上限 15 分钟 |
| 验证 | 新增 `test_jobs.py`（10 个用例：生命周期、失败落错误、404、结果序列化、**严格串行**、异步结果与同步接口逐位一致）；`verify` 新增两项端到端检查 | — |

**顺带发现并修掉一个 gmsh 的硬约束**：`gmsh.initialize()` / `finalize()` 会调用
`signal.signal()`，而**信号处理只能在主线程设置** —— 所以不能在工作线程里
initialize/finalize（会抛 `ValueError: signal only works in main thread`）。
现在改为**主线程初始化一次、进程内复用会话**，每次操作前 `gmsh.clear()`
（见 `backend/gmsh_session.py`）。这同时修掉一个潜在缺陷：以前靠"每请求一次
initialize"获得干净状态，gmsh 一旦复用模型就会不断累积。

### 阶段 2 续 · 材料持久化

| 项 | 落地内容 | 意义 |
|---|---|---|
| 材料持久化 | 新增 `material_store.py`：内置材料仍来自代码（数据库坏了也照样可用），自定义材料写进 **SQLite**（标准库，无新依赖）；默认 `backend/data/simcloud.db`（已 gitignore），可用 `SIMCLOUD_DB` 覆盖 | 此前自定义材料**重启即丢**，多人共用一台机器还互相覆盖 |
| 接口不变 | `GET/POST /api/materials` 的请求与响应形状完全不变 | 前端无需改动；内置 ID 不可被覆盖、重复 ID 返回 400 |
| 验证 | 新增 17 个用例（含"换 store 实例模拟重启"）；**端到端实测**：建材料 → 完整重启后端 → 材料仍在（5→6） | — |
| 更正一处错误结论 | 先前文档称面载荷是"集中式、一阶精度"，实际验证后发现**它本来就是精确的**（`∫N_i dA = A/3`，与 `FacetBasis` 积分相对差异 3.7e-16） | "看起来像近似"≠"真是近似"；结论也要有证据 |

### 阶段 2 续 · 单位制

| 项 | 落地内容 | 意义 |
|---|---|---|
| 长度单位 | `SolverRequest.length_unit`（`m` / `mm`）：求解前把坐标**换算成米**，材料 E（Pa）、载荷（N）与结果（m / Pa）全部落在 SI；响应返回 `units` 声明单位 | 此前坐标被直接当米：10 单位见方的零件算成 10 米，1000 N 只得 **10 Pa**，数值毫无意义 |
| 默认值取舍 | API 默认 `"m"`（不静默改变既有调用方）；前端默认 `"mm"`（CAD 习惯）并提供可见切换控件 | 界面口径友好、接口口径稳定 |
| 连带修复 | 两处写死的**绝对容差**改为相对模型尺度（面中心需按同一比例缩放；"点到平面距离 < 1e-2" 在毫米模型上会选中整个模型） | 单位换算把隐藏的量纲假设全暴露出来了 |
| 验证 | 同一网格按 m/mm 求解：**应力 ×1000²、位移 ×1000**（误差 < 1e-6）；mm 下应力落在 10 MPa 真实量级；非法单位 400 | 端到端 `verify` 也新增两项检查 |

### 阶段 2 续 · 强制位移 + 扩展指南

| 项 | 落地内容 | 意义 |
|---|---|---|
| `displacement` 边界条件 | 逐分量 `fixedX/Y/Z` + 非零值，走 skfem 非齐次 Dirichlet（`condense(x=...)`） | **此前被完全忽略**——用户加了它只会得到"求解成功、结果为零" |
| 验证方式 | 三层断言：指定位移**精确等于**给定值（误差 0）／位移翻倍则支反力精确翻倍／总轴力 `EAδ/L` 比值 1.041 | HTTP 实测同样通过 |
| `tools/tasks.py dev --detach` + `stop` | 后台启动并立即返回，PID 记录在 `.dev-pids.json` | 原 `dev` 会阻塞，脚本/CI 里没法用（本轮就被它挂住过一次） |
| `docs/04-如何扩展求解器.md` | 数据流、新增边界条件/分析类型的完整步骤、**验证判据表**、8 条已知陷阱、提交前清单 | 让"下一个人"知道改哪里、以及怎么证明没改错 |

### 阶段 1 收尾 · 跨平台与容器

> 在此之前，安装/启动/测试/验证**只有 PowerShell 脚本**——Linux 与 macOS 的协作者拿到仓库后完全无从下手。这是当时最大的一条"可协作"阻塞。

| 项 | 落地内容 | 意义 |
|---|---|---|
| 跨平台任务入口 | 新增 `tools/tasks.py`（**纯标准库**）：`setup` / `dev` / `test` / `verify` / `build` / `clean` / `doctor` | Windows / Linux / macOS 同一套命令 |
| `Makefile` | `make setup/dev/test/verify/build/clean` | 开源项目里 contributor 最熟悉的入口 |
| `scripts/*.ps1` 改为薄封装 | 只做解释器探测并转发到 `tasks.py` | **消除两份实现漂移的隐患**（原来 verify 的逻辑只存在于 PowerShell 里） |
| 容器化 | `Dockerfile`（含 Gmsh 所需 `libglu1-mesa` 等系统库）+ `docker-compose.yml` + `.dockerignore` | 跳过本机环境配置；容器里 `UPLOAD_DIR` 因已是绝对路径而不受工作目录影响 |
| CI 加固 | 保持 `python -m unittest` 直接可用，无需额外依赖 | 任务脚本用标准库，CI 不必多装东西 |


### 仍未执行（需你决定）

| 项 | 建议 |
|---|---|
| `frontend/index.html` 的 Tailwind CDN → 构建期编译 | 放到阶段 3。CDN 运行时 + 无 purge 会让生产包偏大、首屏偏慢、离线不可用；但需引入 PostCSS/Tailwind 管线，且 `primary/secondary/accent/text/border` 等自定义类名散落在十几个组件中，要逐个核对样式不丢失 |
| 用真实 Docker 验证一次 `docker build` / `docker compose up` | 开发机没有 Docker，**镜像未经实际构建**；首次构建若报缺库请补 `Dockerfile` 的 apt 清单 |
| 把 CI 接到远程仓库 | `ci.yml` 已就绪但**尚未在 GitHub 上真实执行过**（本地无法运行 Actions）；推上去后需确认「Gmsh 系统库」那一步是否足够 |
| `backend/uploads/零件1.STEP.stl` 等转换产物 | 与 `.msh` 同属可再生缓存，可扩为 `backend/uploads/*.STEP.stl`；需你确认（`test_part.stl` 是刻意保留的样例，性质不同） |

> 提交历史：`e784271`（你原有的改动）、`117accb`（修复+整理+文档）、`6a7df87`（阶段 1 地基）、`05aeffb`（物理正确性）已分别提交，便于回溯。
