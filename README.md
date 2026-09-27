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
│   ├── generate_step.py        # 生成测试件（方块挖通孔）
│   ├── generate_stl.py
│   ├── requirements.txt
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
├── scripts/                    # 一键脚本
│   ├── setup.ps1               # 首次环境安装
│   ├── dev.ps1                 # 同时启动前后端
│   └── verify.ps1              # 类型检查 + 构建 + API 冒烟测试
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

**环境要求**：Python 3.12、Node.js 20+（开发机已装 24.x）

```powershell
# 首次安装（创建 venv、装依赖、生成 .env）
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# 启动前后端（开两个窗口，或直接跑 dev.ps1）
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

访问：**前端 http://localhost:3000** ｜ 后端 http://127.0.0.1:8000 （API 文档 http://127.0.0.1:8000/docs ）

手动启动：

```powershell
# 后端（必须在 backend 目录下运行，uploads/ 是相对路径）
cd backend
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm run dev
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
- 全流程跑通：上传 → 网格 → 求解 → 云图；
- 求解器物理正确性抽查：10×10×10 立方体轴向拉伸，加载面中心位移 `4.17e-10` vs 解析解 `FL/AE = 5e-10`（比值 0.835，全约束端略刚于自由杆，符合预期）；支反力合计与施加载荷精确抵消。

### 已知限制

| 项 | 现状 | 影响 |
|---|---|---|
| 载荷施加 | 总力按面节点**均分**，非面积分 | 峰值应力随网格变化 |
| 边界条件选点 | 平面用"点到平面距离"，曲面退化为包围盒猜测 | 圆柱面等曲面选点不准 |
| 分析类型 | 仅线弹性静力 | 无模态/热/非线性 |
| 材料库 | 内存硬编码，重启丢失 | 无持久化 |
| 单位制 | 直接使用 Gmsh 单位（视为米） | 与 CAD 的 mm 习惯不一致，量级需自行换算 |
| 几何拾取 | 面标签靠包围盒中心近似 | 复杂件上标签可能错位 |
| 结果后处理 | 仅整体云图 | 无剖切/等值面/动画/报告导出 |
| 项目管理 | 前端内存 mock，刷新即丢 | 无登录/数据库 |
| 长任务 | 同步 HTTP 请求 | 大网格会超时 |
| 前端 i18n | 中英文混杂 | 体验不统一 |
| LandingPage | 部分按钮为占位链接 | 无实际功能 |

完整的问题清单与修复过程见 `docs/03-修复记录.md`。

## 6. 安全提醒

- **DeepSeek API Key 曾硬编码在源码中（已泄露），请到平台轮换。** 现在从 `backend/.env` 读取（该文件已被 `.gitignore` 忽略）。
- 后端 CORS 为 `allow_origins=["*"]`，生产环境需收紧。
- `/uploads` 直接拼接用户传入的文件名，存在路径穿越风险，需要做文件名白名单校验。

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/01-开发流程与长期计划.md` | 如何用 DeepSeek Harness 迭代这个项目 + 分阶段路线图 |
| `docs/02-学习路线.md` | 补哪些领域的知识、学到什么程度、对应本项目哪块代码 |
| `docs/03-修复记录.md` | 本次修复的 28 项问题的根因与验证方式（可直接用于项目报告） |
| `scripts/setup.ps1` / `dev.ps1` / `verify.ps1` | 一键安装 / 启动 / 回归验证（含解析解校准） |

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

### 仍未执行（需你决定）

| 项 | 建议 |
|---|---|
| `frontend/index.html` 的 Tailwind CDN → 构建期编译 | 放到 `docs/01` 的阶段 1 或 3。CDN 运行时 + 无 purge 会让生产包偏大、首屏偏慢、离线不可用；但它需要引入 PostCSS/Tailwind 管线，且 `primary/secondary/accent/text/border` 等自定义类名散落在十几个组件中，需逐个核对样式不丢失——超出「最小范围」 |
| `backend/requirements.txt` 版本锁定 | 属阶段 1 的独立任务（`pip freeze` + 回归算例入 CI） |

> 提交建议：当前工作区**还包含你自己未提交的改动**（如 `ImportModal.tsx`、`BoundaryConditionSelector.tsx`，
> 并非本次整理所产生）。建议先 `git diff` 审阅，再分两个提交：① 你原有的改动 ② 本次修复 + 整理 + 文档，便于回溯。
