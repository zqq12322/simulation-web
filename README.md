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
| 求解器 | scikit-fem 12（线性四面体单元，线弹性静力分析） |
| AI | DeepSeek（OpenAI 兼容接口） |
| 存储 | 本地 `uploads/` 目录；Supabase Storage 可选（未配置时自动禁用） |
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
│   ├── config.py               # ★ 集中配置：绝对路径、上传/网格限制、CORS
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
│   └── dist/                   # 生产构建产物
├── docs/                       # 项目文档（先读这里）
│   ├── 01-开发流程与长期计划.md
│   ├── 02-学习路线.md
│   └── 03-修复记录.md
├── tools/
│   └── tasks.py                # ★ 跨平台任务入口：setup / dev / test / verify / build / clean / doctor
├── scripts/                    # Windows 薄封装（转发到 tools/tasks.py，不另写实现）
│   ├── _python.ps1             # 解释器探测（避开 Microsoft Store 的 python 别名）
│   ├── setup.ps1 / dev.ps1 / test.ps1 / verify.ps1
├── Dockerfile                  # 后端镜像（含 Gmsh 所需系统库）
├── docker-compose.yml          # 一键起前后端（开发用）
├── Makefile                    # make setup / dev / test / verify / build / clean
├── .github/workflows/ci.yml    # CI：push / PR 自动跑测试与构建
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
| `Scene3D.tsx` | three.js 视口：点/边/面拾取、约束与载荷可视化、应力云图着色器、支反力面板 |
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

1. 打开 http://localhost:3000 → 「立即开始仿真」→ 新建项目进入工作台；
2. 导入几何（`backend/uploads/test_part.step` 是内置的"方块挖通孔"测试件）；
3. 左侧 `Mesh` → ⚙ → **保存并生成网格**（会真实调用 Gmsh）；
4. `Model → Materials` → 选材料；`Boundary conditions` → 加一个固定约束 + 一个力载荷（在 3D 视口点面即可选中）；
5. `Simulation Runs` → ▶ 求解 → 视口切换为 Von Mises 应力云图 + 图例 + 支反力。

## 4. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 健康检查 |
| GET | `/api/health` | 健康检查（含上传目录绝对路径，供 CI/容器探针使用） |
| GET | `/api/materials` | 材料列表 |
| GET | `/api/materials/{id}` | 单个材料 |
| POST | `/api/materials` | 新增自定义材料（内存，重启丢失） |
| POST | `/api/generate-cube` | 生成 10×10×10 演示立方体 STEP |
| POST | `/api/upload-geometry` | 上传几何；STEP/IGES 自动转 STL 供网页预览 |
| GET | `/api/geometry/{filename}/metadata` | 提取 B-Rep 面/边/顶点（类型、面积、中心、法线） |
| POST | `/api/generate-mesh` | 生成四面体网格，返回节点/单元/面/边/顶点 |
| POST | `/api/solve` | 线弹性静力求解，返回位移/应力/支反力 |
| POST | `/api/validate-setup` | 求解前校验（材料、约束、载荷是否齐全） |
| POST | `/api/ai/chat` | AI 问答 |
| POST | `/api/ai/diagnose` | AI 诊断仿真设置/报错 |
| POST | `/api/ai/configure` | 自然语言 → 结构化仿真参数 JSON |

## 5. 当前状态

已验证通过：

- `tsc --noEmit` 无错误；`vite build` 成功；
- **`scripts/test.ps1`：45 个后端回归用例全部通过**（约 0.3 秒，无需启动服务器）；
- **`scripts/verify.ps1`：11 项端到端检查全部通过**；
- 全流程跑通：上传 → 网格 → 求解 → 云图；
- 求解器物理正确性抽查：10×10×10 立方体轴向拉伸，加载面中心位移 `4.17e-10` vs 解析解 `FL/AE = 5e-10`（比值 0.835，全约束端略刚于自由杆，符合预期）；支反力合计与施加载荷精确抵消。

### 已知限制

| 项 | 现状 | 影响 |
|---|---|---|
| 载荷施加 | 已按**归属面积加权**（节点力之和 = 载荷 × 面积）；`pressure` 也已真正实现 | 曲面上的压力仍需面法向可用，否则会给出警告并忽略 |
| 边界条件选点 | 已从 `.msh` **精确映射**几何实体 → 边界三角形；仅在无映射时才回退到几何搜索 | 由旧版本生成的 `.msh` 可能缺少实体信息 |
| `displacement` 边界条件 | ✅ 已实现（逐分量 `fixedX/Y/Z` + 非零值，走 skfem 非齐次 Dirichlet） | — |
| `temperature` 边界条件 | 未被结构求解器实现（会出现在 `warnings` 中提示）；热分析属阶段 2/4 | 需要时需新增分析类型 |
| 分析类型 | 仅线弹性静力 | 无模态/热/非线性 |
| 材料库 | ✅ 内置材料在代码里，自定义材料持久化到 SQLite（重启不丢） | 无编辑/删除界面（API 层已支持删除） |
| ~~单位制~~ | ✅ 已支持 `m` / `mm`：前端可选，后端换算成米再求解，结果一律 SI（位移 m、应力 Pa） | 换单位后物理结果一致（应力 ×1000²、位移 ×1000） |
| 几何拾取 | 面标签靠包围盒中心近似 | 复杂件上标签可能错位 |
| 结果后处理 | 仅整体云图 | 无剖切/等值面/动画/报告导出 |
| 项目管理 | 前端内存 mock，刷新即丢 | 无登录/数据库 |
| 长任务 | 同步 HTTP 请求 | 大网格会超时 |
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
| `docs/03-修复记录.md` | 本次修复的 28 项问题的根因与验证方式（可直接用于项目报告） |
| `docs/04-如何扩展求解器.md` | **要加功能先读这篇**：求解器数据流、新增边界条件/分析类型的步骤、验证判据表、已知陷阱 |
| `CONTRIBUTING.md` | 协作指南：环境、验证命令、红线、提交规范、如何新增功能、常见问题 |
| `tools/tasks.py` | 跨平台任务入口：`setup` / `dev [--detach]` / `stop` / `test` / `verify` / `build` / `clean` / `doctor` |

## 8. 整理记录

### 已完成（本轮整理）

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

### 阶段 2 续 · 材料持久化（本轮新增）

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
