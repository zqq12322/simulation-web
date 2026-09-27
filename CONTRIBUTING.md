# 贡献指南（CONTRIBUTING）

面向要一起开发这个项目的人。**先读 [README.md](README.md) 了解项目是什么**，
再读本文了解"怎么改才不会被回退"。

---

## 1. 环境要求

| 工具 | 版本 | 说明 |
|---|---|---|
| Python | **3.12** | 后端（gmsh / scikit-fem 均有 3.12 的预编译 wheel） |
| Node.js | **20+** | 前端（开发机实测 24.x） |
| PowerShell | 5.1 或 7+ | 脚本均可直接运行 |

## 2. 首次安装

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

它会：创建 `backend/venv` → 按 `requirements.txt` 安装依赖 → 生成 `backend/.env`
→ `npm install` → 生成 `frontend/.env.local`。

**然后必须做一件事**：打开 `backend/.env` 填入自己的 `DEEPSEEK_API_KEY`
（AI 助手需要；不填只影响 AI 功能，不影响几何/网格/求解）。

## 3. 日常开发

```powershell
# 启动前后端（前端 :3000，后端 :8000，API 文档 :8000/docs）
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

## 4. 四个验证命令，别搞混

| 命令 | 验证什么 | 需要服务在跑吗 | 何时用 |
|---|---|---|---|
| `scripts\test.ps1` | 后端单元 + 物理回归（45 个用例，约 0.3 秒） | **不需要** | 改动任何后端逻辑后**必跑** |
| `scripts\verify.ps1` | 端到端：类型检查 + 真实 HTTP 调用 + 解析解校准 | 需要 | 提交前跑一次 |
| `frontend` 下 `npm run build` | 前端能否构建 | 不需要 | 改前端后 |
| CI（`.github/workflows/ci.yml`） | 上面几项的自动化版本 | 不需要 | push / PR 时自动跑 |

**红线：改动 `backend/solver.py` 或 `backend/geometry.py` 后，必须让
`scripts\test.ps1` 通过。** 其中的求解器回归测试会用解析解 `FL/AE` 与
支反力守恒来校验结果——这套仿真的价值全在"结果是对的"，破坏它比写出 bug 更糟。

## 5. 代码结构导航

```
backend/
├── config.py          # ★ 所有路径与可调参数都在这；UPLOAD_DIR 是绝对路径
├── main.py            # FastAPI 应用装配、CORS、静态 uploads/、健康检查
├── geometry.py        # 上传 / STEP 转 STL / B-Rep 元数据 / 网格生成
├── solver.py          # 线弹性静力求解（scikit-fem）
├── materials.py       # 材料库
├── constraints.py     # 边界条件模型与设置校验
├── ai_assistant.py    # DeepSeek 助手
├── supabase_client.py # 可选云存储
└── tests/             # unittest 测试（无需服务器）
frontend/
├── index.tsx          # React 入口（注意不是 src/main.tsx）
├── App.tsx            # 落地页 → 仪表盘 → 工作台
├── types.ts           # 全局类型契约
└── components/        # 11 个组件，Workbench 是总调度、Scene3D 是 3D 视口
```

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
| `python -c` 之类命令被拒绝执行 | 沙箱策略拦截，不是命令写错；按提示申请权限或换等价写法 |
| 端口 3000/8000 被占用 | `scripts\dev.ps1` 会提示并跳过；先关掉旧的窗口 |
| 求解返回 `status: solved` 但应力全是 0 | 检查是否真的加了载荷；`POST /api/validate-setup` 会指出缺失项 |
| 网格生成很慢或超时 | 调大 `mesh_size`；长任务异步化仍是待办（见 `docs/01`） |
| 换了工作目录后 `uploads/` 找不到 | 已修复：`UPLOAD_DIR` 现在是基于 `config.py` 的绝对路径 |

## 10. 下一步该做什么

见 [`docs/01-开发流程与长期计划.md`](docs/01-开发流程与长期计划.md) 的阶段划分，
以及 [README.md](README.md) 第 8 节的整理记录与待办。
当前最值得投入的方向：**真实面力积分**、**用 Physical Groups 精确绑定边界条件**、
**长任务异步化**。
