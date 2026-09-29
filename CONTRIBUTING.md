# 贡献指南（CONTRIBUTING）

面向要一起开发这个项目的人。**先读 [README.md](README.md) 了解项目是什么**，
再读本文了解"怎么改才不会被回退"。

---

## 1. 环境要求

| 工具 | 版本 | 说明 |
|---|---|---|
| Python | **3.9+**（推荐 3.12） | 后端 + 任务脚本（`tools/tasks.py` 只用标准库） |
| Node.js | **20+** | 前端（开发机实测 24.x） |
| Docker（可选） | 任意近期版本 | 想跳过本机环境配置时用 |
| PowerShell（仅 Windows 需要） | 5.1 或 7+ | `scripts\*.ps1` 是薄封装 |

> **跨平台**：安装/启动/测试/验证的**唯一实现**是 `tools/tasks.py`（纯标准库 Python），
> Windows / Linux / macOS 通用。`scripts\*.ps1` 只是 Windows 上的转发封装，
> 不再各自实现一套逻辑，避免两份实现逐渐漂移。

## 2. 首次安装

```bash
# Linux / macOS（有 make）
make setup

# 任意平台（没有 make 也行）
python3 tools/tasks.py setup      # Windows 上用 py -3 或 venv 里的 python

# Windows 习惯的写法（等价，转发到同一个实现）
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

它会：创建 `backend/venv` → 按 `requirements.txt` 安装依赖 → 生成 `backend/.env`
→ `npm install` → 生成 `frontend/.env.local`。

**然后必须做一件事**：打开 `backend/.env` 填入自己的 `DEEPSEEK_API_KEY`
（AI 助手需要；不填只影响 AI 功能，不影响几何/网格/求解）。

先体检一下环境可以用：

```bash
python3 tools/tasks.py doctor     # 检查 python / node / git / venv / .env / 服务状态
```

### 2.1 完全不想配本机环境（Docker）

```bash
docker compose up --build
# 前端 http://localhost:3000   后端 http://localhost:8000/docs
```

镜像里已经装好 Gmsh 需要的系统库（`libglu1-mesa` 等），这是手工配环境最容易翻车的一步。

> ⚠️ 注意：`Dockerfile` / `docker-compose.yml` **尚未在真实 Docker 上验证过**
> （开发机没有装 Docker）。语法已用 YAML 解析器校验通过，但首次 `docker build`
> 若报缺库，请补充 `Dockerfile` 里的 `apt-get install` 清单。

## 3. 日常开发

```bash
make dev                # 或 python3 tools/tasks.py dev
# 前端 http://localhost:3000   后端 http://localhost:8000   文档 /docs
# Ctrl+C 同时停掉前后端
```

（Windows 也可以用 `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1`。）

## 4. 四个验证命令，别搞混

| 命令（跨平台） | Windows 等价 | 验证什么 | 需要服务在跑吗 | 何时用 |
|---|---|---|---|---|
| `make test` / `python3 tools/tasks.py test` | `scripts\test.ps1` | 后端单元 + 物理回归（**513** 个用例，约 14 秒） | **不需要** | 改动任何后端逻辑后**必跑** |
| `make verify` / `python3 tools/tasks.py verify` | `scripts\verify.ps1` | 端到端：类型检查 + 真实 HTTP + 解析解校准 + 认证/隔离/配置/共享（**149** 项） | 需要 | 提交前跑一次 |
| `make build` | — | 前端类型检查 + 生产构建 | 不需要 | 改前端后 |
| `python3 tools/tasks.py clone-verify` | — | **干净检出**（只有被跟踪的文件）里的单元测试 + `verify` | 不需要（它自己起一个临时后端，端口自动选） | 改动可能依赖生成物时；推送前 |
| `python3 tools/tasks.py check-ci` | — | CI 能不能跑起来里**本地查得了的部分**（见下） | 不需要（要联网） | 推送前 |
| CI（`.github/workflows/ci.yml`） | — | 上面几项的自动化版本 | 不需要 | push / PR 时自动跑 |

**`check-ci` 查什么**：CI 第一步（装依赖）的失败**本地永远复现不了**——本地依赖早就
装好了，`pip install -r` 只会说 "Requirement already satisfied"，所以"pins 在 PyPI 上
真的存在"这件事只能主动去核对。它做四件事：

1. `requirements.txt` / `requirements.lock.txt` 里**每个 pin 去 PyPI 核对是否存在**
   （版本写错、被 yank、名字拼错 → CI 第一步就红）；
2. `npm ci --dry-run` 核对 `package-lock.json` 与 `package.json` **是否同步**
   （不同步的话 npm 会直接 EUSAGE 硬失败）；
3. workflow 的 job 齐不齐、关键命令在不在、**引用到的路径是否都存在且进了版本库**
   （干净检出里没有未跟踪文件）；
4. **如实列出本地查不了的部分**：bash 片段的语法（本机可能没有 bash）、apt 包在
   runner 上是否可得、runner 的 Node/Python 版本、各 action 的可用性。

第 3 项已经**离线**进了 `verify`（每次跑都盯着 CI 配置漂移），所以 CI 配置被改坏
会在 `make verify` 里当场红，而不是等推上去。第 1、2 项要联网，留在 `check-ci` 里。

**可选依赖 `pyflakes`**（`pip install pyflakes`）：`verify` 里有一条检查用它查
`tools/tasks.py` 的未定义名字。没装就跳过并在说明里写明——**CI 会装，所以那条在
CI 上始终生效**。为什么专门查这一个文件：`ast.parse` 只查语法、不查名字解析，而
`tools/tasks.py` 是唯一没有单元测试覆盖、且"未定义名字"代价最大的地方（每个任务
都要把活干完才炸——这事真发生过一次）。

### 断言有没有牙：`tools/mutation_check.py`

前端纯函数的自检全绿有两种可能：**代码是对的**，或者**断言没长牙**（写了 `check`
但条件恒真）。从输出上完全看不出来，所以有一个单独的工具：

```
python3 tools/mutation_check.py
```

它把实现故意改坏一点点（当前 7 处），看自检是否报错。**不进 CI**：它会临时改动源
文件（用 `try/finally` 还原），是人工使用的诊断工具——别在跑它的时候编辑那几个
文件。

这不是理论担忧：第一次跑就抓到一条没有牙的断言——把用户提示里的"超过服务端保留
上限"（**原因**）删掉，所有断言仍然通过。用户看到"未保留"却不知道原因，就无从判断
该不该重试，而测试当时完全没在看这件事。

**新写自检时的建议**：先问自己"这条断言在什么情况下会红"——答不上来，就用这个
工具量一下。

**CI 跑三件事**（三个**并行** job，互不依赖）：后端单元测试；前端类型检查与构建；
**端到端验证（`verify`）**。第三个是本项目主要证据的所在地——解析解物理校准、
收敛阶、导出文件的逐位回读都在 `verify` 里，**不在**单元测试里。它需要 Node ≥ 23
（`verify` 会用 node 直接执行前端的 `.ts` 纯函数），所以那个 job 单独装了 Node 24。

**红线：改动 `backend/solver.py` 或 `backend/geometry.py` 后，必须让测试通过。**
其中的回归测试用解析解 `FL/AE`、**解析应力场 `Von Mises = 2με`** 与支反力守恒来
校验结果——这套仿真的价值全在"结果是对的"，破坏它比写出 bug 更糟。
（历史上正因为只断言"应力有限"，漏掉了一个把应力放大 1e7 倍的错误，
详见 `docs/03-修复记录.md` 第五节。）

**另一条容易踩的：改动涉及"生成物"时先 `clean` 再 `test`。**

```
python3 tools/tasks.py clean    # 删掉 *.msh 等可再生缓存
python3 tools/tasks.py test
```

`backend/uploads/*.msh` 是 gitignore 的可再生产物。本机跑测试时它们通常已经存在，
于是测试可能**悄悄依赖**这些缓存——本地一直绿，干净检出里却直接
`FileNotFoundError`。这个坑真的发生过一次（`test_convergence_study.py` 依赖
`test_part.step.msh` 存在），是 CI 的干净检出第一次把它照出来的：
**`clean` + `test` 就是最便宜的本地复现方式。**

### 4.1 提 PR 时请用模板

`.github/PULL_REQUEST_TEMPLATE.md` 会自动出现。它会要求你填**解析解对照表**——
这不是形式主义：本项目 4 个致命缺陷（`meshio` 缺失、NumPy 2 的 `np.cross` 轴序、
Von Mises 放大 1e7 倍、云图不显示变形）**没有一个**是静态检查能发现的。

### 4.2 写测试时的两个硬约束

1. **不要让后台任务留在工作线程里跑。**
   gmsh 的状态是进程级全局的，本项目约定所有 gmsh 操作都在单线程工作器里串行
   （见 `backend/jobs.py`）。如果某个测试在中途断言失败、把任务留在工作线程继续跑，
   而下一个测试又在主线程调用 `gmsh.open`，两边并发访问同一份 gmsh 全局状态，
   结果是 `OSError: access violation reading 0x0` —— 一个与真实失败原因毫无关系的崩溃。
   **正确写法**：先轮询到任务终态再断言；并在 `tearDown` 里排空队列
   （见 `backend/tests/test_modal.py` 的 `_drain_job_queue`）。

2. **不要为了让测试通过而放宽阈值**，也不要断言"结果有限 / 不是 NaN"当作验证。
   要给出与解析解、守恒律或精确不变量的**量化**对照。
   如果你发现自己写的断言失败了，先怀疑断言：本项目已经出现三次
   "测试写错了、代码是对的"（把 `λ+2μ` 误当 `E`、把频率缩放误当"应完全相同"、
   跨尺寸比较绝对幅度）——三次都在 `docs/03-修复记录.md` 里有记录。
   但也别走向反面：**顺手加的边界值用例是有价值的**。项目里
   `formatFrequency(null)` 曾返回 `0 Hz`（把"数据缺失"显示成"刚体模态"），
   就是一条 `[null, '—']` 的边界断言抓到的。

**要加新功能（边界条件/分析类型）之前，先读 [`docs/04-如何扩展求解器.md`](docs/04-如何扩展求解器.md)** ——
里面有数据流、分步做法、验证判据表和已知陷阱。

需要后台起服务（例如在脚本/CI 里接着跑 verify）：

```bash
python3 tools/tasks.py dev --detach   # 启动后立即返回
python3 tools/tasks.py verify
python3 tools/tasks.py stop           # 收干净
```

## 5. 代码结构导航

```
backend/
├── config.py          # ★ 所有路径与可调参数都在这；UPLOAD_DIR 是绝对路径
├── main.py            # FastAPI 应用装配、CORS、静态 uploads/、健康检查
├── geometry.py        # 上传 / STEP 转 STL / B-Rep 元数据 / 网格生成
├── solver.py          # 线弹性静力求解（scikit-fem）
├── thermal.py         # 稳态热传导（∇·(k∇T)=0）
├── modal.py           # 模态分析（K φ = λ M φ，一致质量矩阵）
├── fe_utils.py        # 共享 FE 基础设施：读网格、归属面积、面→节点定位、矢量解析
├── jobs.py            # 后台任务：单线程工作器 + /api/jobs/*
├── gmsh_session.py    # Gmsh 会话（主线程初始化一次，进程内复用）
├── passwords.py       # ★ 口令哈希（scrypt；参数写进哈希串，可平滑升级）
├── auth_store.py      # ★ 用户与会话（令牌只存哈希、可吊销、SQL 里判过期）
├── auth.py            # ★ 注册/登录/登出/me + require_user 依赖
├── sqlite_store.py    # ★ SQLite 存储基类：连接/事务/**关闭** + 幂等加列迁移
├── material_store.py  # 材料持久化（继承 sqlite_store）
├── project_store.py   # 项目持久化（继承 sqlite_store）
├── materials.py       # 材料库
├── projects.py        # 项目管理 API（/api/projects 增删改查，需登录）
├── constraints.py     # 边界条件模型与设置校验
├── ai_assistant.py    # DeepSeek 助手
├── supabase_client.py # 可选云存储
└── tests/             # unittest 测试（无需服务器）
frontend/
├── index.tsx          # React 入口（注意不是 src/main.tsx）
├── App.tsx            # 落地页 → 登录 → 仪表盘 → 工作台
├── types.ts           # 全局类型契约
├── components/        # 含 AuthPanel（登录/注册）、ShareModal（共享管理）、resultShader（云图 GLSL）
└── utils/             # 不依赖框架的纯函数（verify 会用 node 直接跑它们）
    ├── authApi.ts       # 令牌存取 / 请求头 / 401 与网络错误的区分
    ├── deformation.ts   # 变形放大系数
    ├── modalModes.ts    # 模态阶次列表 / 频率格式化 / 振型取场
    ├── modelSource.ts   # 几何取用：带认证下载 → blob URL（含失败文案与 revoke）
    ├── projectSetup.ts  # 项目配置组装 / 稳定签名 / 恢复校验 / 保存状态文案
    ├── projectsApi.ts   # 项目记录的接口↔界面映射、**角色判权限**、错误翻译
    └── projectsApi.ts   # 项目记录的接口↔界面映射、属主判定、错误翻译
tools/
└── tasks.py           # ★ 跨平台任务入口（setup/dev/test/verify/build/clean/doctor）
Dockerfile             # 后端镜像（含 Gmsh 系统库）
docker-compose.yml     # 前后端一键起
Makefile               # make setup / dev / test / verify / build / clean
scripts/               # Windows 薄封装，转发到 tools/tasks.py
```

> 新增开发任务时**请改 `tools/tasks.py`**，不要在 `scripts/*.ps1` 里另写一套实现。

## 6. 红线（会被 review 打回的做法）

1. **任何密钥、Token、密码都不能写进源码或提交进仓库。**
   本项目历史上曾把真实 DeepSeek Key 硬编码在 `ai_assistant.py`，已经泄露过一次。
   现在统一走 `backend/.env`（已被 `.gitignore` 忽略），并且
   `tests/test_validation.py` 里有一道自动化闸门会扫描源码中的 `sk-` 字面量。
2. **不要提交**：`backend/.env`、`backend/uploads/*.msh`、`node_modules/`、
   `dist/`、任何虚拟环境。这些都已写进 `.gitignore`。
3. **不要绕过测试**：不要为了让测试通过而放宽断言阈值（例如把 `FL/AE` 的
   区间改成 `(0, 999)`）。若确实需要调整，请在 PR 描述里说明**物理或数值上的理由**。
4. **不要删除或弱化** `resolve_upload_path` 的路径校验——它挡住了路径穿越。
5. 改动前端目录结构时，记得同步 `vercel.json` 与 `scripts/*.ps1` 里的路径。

## 7. 提交与分支规范

- 分支：`main` 保持随时可运行；新功能/修复开 `feat/xxx`、`fix/xxx`、`docs/xxx` 分支。
- 提交信息用 **Conventional Commits + 中文描述**：

```
<type>(<scope>): <一句话说明>

<为什么这么改 / 影响范围，必要时列出验证方式>
```

`type` 取 `feat` `fix` `docs` `refactor` `test` `chore` `perf`。
示例见 `git log`：`fix: 修复致命缺陷、整理工程结构并补齐文档`。

- 一个提交只做一件事。**不要把自己的在途改动和别人的修复混在一个提交里**
  （本项目就出现过这种交错：`Workbench.tsx` 同时含两人的改动，最后只能在
  提交信息里注明）。

## 8. 如何新增功能

**新增一个 API 端点**：

1. 在对应模块（如 `geometry.py`）里加 `@router.post("/your-endpoint")`，
   参数与返回值都用 Pydantic 模型声明；
2. 用户可控的**文件名一律走 `config.resolve_upload_path()`**，数值参数在
   `config.py` 里加校验函数；
3. 在 `backend/tests/` 里加测试——**不需要启动服务器**，直接 `asyncio.run(你的函数(...))`；
4. 在 `README.md` 的 API 表格里补一行。

**新增一个测试**：

```python
# backend/tests/test_xxx.py
import asyncio, unittest
from geometry import your_function

class YourTest(unittest.TestCase):
    def test_something(self):
        result = asyncio.run(your_function("test_part.step"))
        self.assertEqual(result.status, "success")
```

测试夹具用仓库里已有的 `backend/uploads/test_part.step`（方块挖通孔）和
`default_cube.step`（10×10×10 立方体，有解析解），这样 CI 上无需外部文件。

## 9. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `.ps1` 脚本报 `Unexpected token '}'` | 脚本被存成了**无 BOM 的 UTF-8**，Windows PowerShell 5.1 会按 ANSI 解析中文。用编辑器另存为 "UTF-8 with BOM"（`.editorconfig` 已声明） |
| Windows 上跑 `python tools/tasks.py ...` 毫无输出、也不报错 | 你的 `python` 是 Microsoft Store 的**应用执行别名**（`WindowsApps\python.exe`）。用 `py -3` 或后端 venv 里的 `python.exe`；`scripts\*.ps1` 已内置优先级处理 |
| `tools/tasks.py verify` 在最后一行抛 `UnicodeEncodeError` | 已在 `_configure_streams()` 里把输出流设为 `errors="replace"`；若仍遇到，说明有新加的字符，请同时避免使用 emoji |
| `docker compose up` 报缺 `libGL`/`libXrender` 之类的库 | `Dockerfile` 的 apt 清单需要补库；该镜像**尚未在真实 Docker 上验证过** |
| `python -c` 之类命令被拒绝执行 | 沙箱策略拦截，不是命令写错；按提示申请权限或换等价写法 |
| 端口 3000/8000 被占用 | `dev` 任务会提示并跳过；先关掉旧的进程 |
| 求解返回 `status: solved` 但应力全是 0 | 检查是否真的加了载荷；`POST /api/validate-setup` 会指出缺失项 |
| 求解返回里 `warnings` 非空 | 有边界条件被忽略/降级（如 `temperature`、未选中节点），前端会弹黄色横幅 |
| 网格生成很慢或超时 | 调大 `mesh_size`。网格/求解都已异步化（`/api/jobs/*`），前端会轮询 |
| 换了工作目录后 `uploads/` 找不到 | 已修复：`UPLOAD_DIR` 现在是基于 `config.py` 的绝对路径 |
| 测试报 `OSError: access violation reading 0x0` | 有后台任务还在工作线程里跑 gmsh，主线程又并发调用了 gmsh。这不是"gmsh 装坏了"：先轮询到任务终态再断言，并在 `tearDown` 里排空队列（见 §4.2 第 1 条） |
| 模态分析里"加了载荷但频率没变" | **这是正确的**：线性模态分析的固有频率与载荷幅值无关，载荷类边界条件会被忽略并给出警告 |
| 模态分析报 `num_modes` 超范围 | 允许 1–30。约束过多导致可求自由度不足时也会报 400 |
| 用 PowerShell 手工调接口时中文变乱码 | **不是后端的问题**。Windows PowerShell 5.1 的 `Invoke-RestMethod`：发 body 时按 ANSI 编码 ⇒ 中文变 `?`；解析 JSON 响应时按 ISO-8859-1 解码 ⇒ 中文变 `éå¯...`。直接读 SQLite 会看到存的是正确的中文。断言编码相关行为请用 Python（`tools/tasks.py` 里的 `_http_json` 显式 `encode/decode('utf-8')`）或浏览器 |
| 项目列表空了 / 报"无法连接后端" | 项目存在 `backend/data/simcloud.db`（可用 `SIMCLOUD_DB` 改）。先确认后端在跑；这是**唯一**一份数据，删掉它项目就没了 |
| 忘了口令 / 想把账号清掉 | 没有"重置口令"的界面（也没有找回邮件）。单人自用可以直接删库重来：停服务 → 删 `backend/data/simcloud.db` → 重启（**项目与自定义材料会一起没有**）。要保留项目就先备份该文件 |
| 看到项目标着"未归属"，改名/删除按钮不见了 | 那是**接上登录之前**创建的项目（`owner_id IS NULL`）。策略是"可见但不可改"，点卡片上的「认领」即可；这是刻意设计——初版做过"第一个注册的用户自动接管"，结果把用户的项目静默划给了 `verify` 的测试账号 |
| 列表里出现 `verify_alice` / `verify_bob` | 这是 `tools/tasks.py verify` 用的**固定测试账号**（登录优先，不存在才注册），故意不每次新建用户。它们只会看到自己的项目 |
| 接口返回 401 但口令明明是对的 | 先看是不是令牌过期（默认 30 天，`SIMCLOUD_TOKEN_TTL_DAYS` 可调）；前端在启动时会调 `/api/auth/me` 校验，失效就回登录页。另外注意"连不上后端"与 401 是两回事，前端会分别提示 |
| 用 `curl` 调接口一律返回 401 | **除 `/`、`/api/health`、`/api/auth/*` 外，所有端点都要求登录**（八个计算类 router 用路由级依赖统一保护）。先 `curl -X POST .../api/auth/login -d '{"username":..,"password":..}'` 拿 token，再加 `-H "Authorization: Bearer $TOKEN"` |
| `git status` 显示文件被改了，`git diff` 却是空的 | 索引的 stat 缓存过期（常见于用脚本改过文件、或换行符被规范化过）。`git add <这些文件>` 刷新即可——内容其实没变，不会产生改动 |
| 3D 视口是空的 / 提示"几何预览加载失败" | 几何预览走的是**需登录**的 `/api/geometry/{文件名}/download`（公开的 `/uploads/` 已关闭）。先看提示里的具体原因：401 是登录失效、404 是文件被清理过（重新导入即可）。仿真配置、网格与求解不受影响 |
| 别人看不到我共享的项目 | 共享在仪表盘卡片上的「共享」按钮里（**只有属主**有）；用的是**用户名**不是用户 id。被共享的人刷新后会在列表里看到，并带"共享 · 可编辑/只读"标记 |
| 关掉页面再打开项目，配置没了 | 不应该发生（配置会自动保存到后端）。先看标题栏的保存状态：若是"保存失败"就点重试。数据存在 `backend/data/simcloud.db` 的 `projects.setup` 列；`GET /api/projects/{id}/setup` 可核对 |

## 10. 下一步该做什么

见 [`docs/01-开发流程与长期计划.md`](docs/01-开发流程与长期计划.md) 的阶段划分，
以及 [README.md](README.md) 第 8 节的整理记录与待办。

「阶段 2 · 让仿真结果可信」的物理项已全部完成（真实面力、精确绑面、单位制、
材料持久化、强制位移、稳态热传导、**模态分析**），剩下的阶段 2 项目是
**网格质量与收敛性**。再往后最值得投入的是：

1. **模态分析的前端接线**（选振型、按位移着色）——后端已就绪，界面上还没有入口；
2. **给求解/上传端点也加上登录要求**（目前只有项目与认证端点要求登录，求解端点仍开放）；
3. **结果后处理**：剖切面、等值面、变形动画、CSV/VTK/PNG 导出；
4. **网格质量直方图与 h 收敛性检查**（阶段 2 收尾）。
