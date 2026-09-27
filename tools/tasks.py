#!/usr/bin/env python3
"""
跨平台开发任务入口（Windows / Linux / macOS 通用）。

在此之前，项目的安装/启动/测试/验证全部只有 PowerShell 脚本，
Linux 与 macOS 的协作者拿到仓库后无从下手。这里用**纯标准库**实现了
同一套任务，任何装了 Python 3.9+ 的机器都能跑：

    python tools/tasks.py setup     # 创建 venv、安装依赖、生成 .env
    python tools/tasks.py dev       # 同时启动前后端（Ctrl+C 一起停）
    python tools/tasks.py test      # 后端回归测试（不需要启动服务）
    python tools/tasks.py verify    # 端到端验证：类型检查 + 真实 HTTP + 解析解校准
    python tools/tasks.py build     # 前端生产构建
    python tools/tasks.py clean     # 清理构建产物与可再生缓存

设计约定：**本文件是这些任务的唯一实现**。`scripts/*.ps1` 只是 Windows 上的
薄封装，转调这里，避免两份实现各自漂移。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"
API_BASE = os.environ.get("SIMCLOUD_API", "http://127.0.0.1:8000")
FRONTEND_URL = os.environ.get("SIMCLOUD_FRONTEND", "http://localhost:3000")

#: `dev --detach` 记录子进程 PID，供 `stop` 使用
_STATE_FILE = ROOT / ".dev-pids.json"

#: 服务端口（后端 8000 / 前端 3000），`stop` 会按端口兜底清理
_SERVICE_PORTS = (8000, 3000)

# ---------------------------------------------------------------- 输出helpers

def _configure_streams() -> None:
    """
    让输出在任意终端 / CI 下都不会因编码而崩。

    背景：Windows 控制台默认使用本地代码页（简中为 GBK），打印 emoji 或某些
    字符会直接抛 UnicodeEncodeError 把整个任务打断——本轮就踩到了
    （verify 全部 11 项通过，却在最后一行打印 ✅ 时崩掉，退出码变成 1）。
    这里把无法编码的字符替换掉，保证"验证结果"不会因为一个表情符号而丢失。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # 老版本 Python 或已被重定向的流，忽略即可


def _c(text: str, color: str) -> str:
    """给终端输出上色（不支持时自动降级为纯文本）。"""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "dim": "2"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def info(message: str) -> None:
    print(_c(message, "cyan"))


def ok(message: str) -> None:
    print(_c(message, "green"))


def warn(message: str) -> None:
    print(_c(message, "yellow"))


def fail(message: str) -> None:
    print(_c(message, "red"))


def run(cmd: Iterable[str], cwd: Optional[Path] = None, check: bool = True) -> int:
    """运行子进程并把输出直接透传到终端。"""
    cmd = [str(part) for part in cmd]
    print(_c("$ " + " ".join(cmd), "dim"))
    completed = subprocess.run(cmd, cwd=str(cwd or ROOT))
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.returncode


# ------------------------------------------------------------- 解释器定位

def venv_python() -> Path:
    candidate = (
        BACKEND / "venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    )
    return candidate


def require_venv() -> Path:
    python = venv_python()
    if not python.exists():
        fail(f"未找到后端虚拟环境：{python}")
        fail("请先运行：python tools/tasks.py setup")
        raise SystemExit(1)
    return python


def require_node() -> str:
    node = shutil.which("node")
    if not node:
        fail("未找到 node，请先安装 Node.js 20+")
        raise SystemExit(1)
    return node


# --------------------------------------------------------------------- 任务

def task_setup(args: argparse.Namespace) -> int:
    python = venv_python()

    if not python.exists():
        info("[1/4] 创建后端虚拟环境 backend/venv ...")
        run([sys.executable, "-m", "venv", str(BACKEND / "venv")])
    else:
        ok("[1/4] 后端虚拟环境已存在，跳过")

    info("[2/4] 安装后端依赖（首次约 5-10 分钟：gmsh / scipy / scikit-fem）...")
    run([python, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    run([python, "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")])

    env_file = BACKEND / ".env"
    if not env_file.exists():
        info("[3/4] 生成 backend/.env（AI 助手需要 DEEPSEEK_API_KEY）")
        shutil.copyfile(BACKEND / ".env.example", env_file)
    else:
        ok("[3/4] backend/.env 已存在，跳过")

    info("[4/4] 安装前端依赖 ...")
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    run([npm, "install"], cwd=FRONTEND)
    frontend_env = FRONTEND / ".env.local"
    if not frontend_env.exists():
        shutil.copyfile(FRONTEND / ".env.example", frontend_env)

    ok("\n安装完成。下一步：python tools/tasks.py dev")
    return 0


def task_test(args: argparse.Namespace) -> int:
    python = require_venv()
    info("=== 后端回归测试（不需要启动服务器） ===")
    return run([python, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=BACKEND)


def task_build(args: argparse.Namespace) -> int:
    require_node()
    info("=== 前端类型检查 ===")
    run([require_node(), "node_modules/typescript/bin/tsc", "--noEmit"], cwd=FRONTEND)
    info("\n=== 前端生产构建 ===")
    run([require_node(), "node_modules/vite/bin/vite.js", "build"], cwd=FRONTEND)
    ok("\n构建完成（产物在 frontend/dist）")
    return 0


def task_dev(args: argparse.Namespace) -> int:
    """
    启动前后端。

    默认前台运行，Ctrl+C 一起停（适合人工开发）。
    加 ``--detach`` 则后台运行并立即返回（适合脚本 / CI：启动后接着跑 verify）。
    """
    python = require_venv()
    require_node()
    npm = "npm.cmd" if IS_WINDOWS else "npm"

    backend_cmd = [
        str(python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000",
    ]
    frontend_cmd = [npm, "run", "dev"]

    if args.detach:
        return _dev_detached(backend_cmd, frontend_cmd)

    info(f"启动后端 {API_BASE} ...")
    backend = subprocess.Popen(backend_cmd, cwd=str(BACKEND))
    info(f"启动前端 {FRONTEND_URL} ...")
    frontend = subprocess.Popen(frontend_cmd, cwd=str(FRONTEND))

    try:
        for _ in range(30):
            time.sleep(1)
            if _health_ok():
                ok("后端已就绪")
                break
        else:
            warn("后端 30 秒内未就绪，请查看上面的输出")

        _print_urls()
        print("按 Ctrl+C 停止两个服务。")

        backend.wait()
        frontend.wait()
    except KeyboardInterrupt:
        info("\n正在停止服务 ...")
    finally:
        for process in (frontend, backend):
            _terminate(process)
    return 0


def _dev_detached(backend_cmd: list, frontend_cmd: list) -> int:
    """后台启动前后端，等后端就绪后返回；PID 写入 .dev-pids.json 供 stop 使用。"""
    busy = _ports_in_use(_SERVICE_PORTS)
    if busy:
        warn(
            f"端口 {busy} 已被占用——很可能已有服务在跑。"
            "若确实是遗留进程，先执行 `stop`，否则本次启动会失败并写入错误的 PID。"
        )
    if _STATE_FILE.exists():
        warn(f"检测到 {_STATE_FILE.name}，可能已有后台服务（先 stop 或删除该文件）")

    backend_log = ROOT / ".dev-backend.log"
    frontend_log = ROOT / ".dev-frontend.log"

    info(f"后台启动后端（日志：{backend_log.name}）...")
    backend = _spawn_detached(backend_cmd, BACKEND, backend_log)
    info(f"后台启动前端（日志：{frontend_log.name}）...")
    frontend = _spawn_detached(frontend_cmd, FRONTEND, frontend_log)

    _STATE_FILE.write_text(
        json.dumps({"backend": backend.pid, "frontend": frontend.pid}), encoding="utf-8"
    )

    for _ in range(30):
        time.sleep(1)
        if _health_ok():
            ok("后端已就绪")
            break
    else:
        warn(f"后端 30 秒内未就绪，请查看 {backend_log}")

    _print_urls()
    print(f"已在后台运行（PID 记录在 {_STATE_FILE.name}）。停止：python3 tools/tasks.py stop")
    return 0


def task_stop(args: argparse.Namespace) -> int:
    """
    停止后台服务。

    两道保险，原因都是真实踩过的：

    1. **不能只信 `.dev-pids.json`**：如果先前遗留了别的进程占着端口，
       `dev --detach` 会启动失败却仍写入新的 PID，状态文件于是指向一个
       "没在监听"的进程，真正的服务反而活着（本轮 verify 就是被这种陈旧
       进程用**旧代码**服务的，表现为响应里缺字段）。
    2. **一次 kill 可能不够**：Windows 上 `python -m uvicorn` 会出现
       "启动器 + 实际监听"两个 python 进程，杀掉其中一个，另一个仍持有
       监听套接字。所以按端口反复"查—杀"直到真正释放。
    """
    stopped: list[str] = []

    if _STATE_FILE.exists():
        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warn(f"{_STATE_FILE.name} 读取失败（忽略）：{exc}")
            state = {}
        for name, pid in state.items():
            if _kill_tree(int(pid)):
                stopped.append(f"{name}(PID {pid})")
        _STATE_FILE.unlink(missing_ok=True)
    else:
        info(f"未找到 {_STATE_FILE.name}，改为按端口清理")

    for _ in range(6):
        remaining = sorted({pid for port in _SERVICE_PORTS for pid in _listening_pids(port)})
        if not remaining:
            break
        for pid in remaining:
            if _kill_tree(pid):
                stopped.append(f"端口占用进程(PID {pid})")
        time.sleep(0.5)

    if stopped:
        ok("已停止：" + "、".join(dict.fromkeys(stopped)))

    still_busy = {port: pids for port in _SERVICE_PORTS if (pids := _listening_pids(port))}
    if still_busy:
        warn(f"仍有进程占用端口：{still_busy}，请手动结束它们")
        return 1
    if not stopped:
        info("没有发现需要停止的服务")
    return 0


def _listening_pids(port: int) -> list:
    """
    找出正在监听某端口的进程 PID（跨平台，只用系统自带工具）。

    Windows 用 ``netstat -ano``；POSIX 用 ``lsof``（缺失时返回空表，
    调用方会退化为"按 PID 文件停止"）。
    """
    pids: list = []
    try:
        if IS_WINDOWS:
            output = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True
            ).stdout
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    if parts[1].endswith(f":{port}"):
                        pids.append(int(parts[4]))
        else:
            output = subprocess.run(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True,
            ).stdout
            pids = [int(token) for token in output.split() if token.strip().isdigit()]
    except (OSError, ValueError):
        return []
    return sorted(set(pids))


def _ports_in_use(ports) -> list:
    return [port for port in ports if _listening_pids(port)]


def _spawn_detached(cmd: list, cwd: Path, log_path: Path):
    """跨平台地启动一个脱离父进程的子进程，输出重定向到日志文件。"""
    log = open(log_path, "ab")
    kwargs: dict = {"cwd": str(cwd), "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _kill_tree(pid: int) -> bool:
    """结束进程及其子进程（Windows 上用 taskkill /T，POSIX 上用进程组）。"""
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        os.killpg(os.getpgid(pid), 15)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _print_urls() -> None:
    print()
    print(_c("=" * 52, "cyan"))
    print(f"  前端   : {FRONTEND_URL}")
    print(f"  后端   : {API_BASE}")
    print(f"  API文档: {API_BASE}/docs")
    print(_c("=" * 52, "cyan"))


def task_clean(args: argparse.Namespace) -> int:
    removed = []
    for path in [
        FRONTEND / "dist",
        *(BACKEND / "uploads").glob("*.msh"),
    ]:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path.relative_to(ROOT)))
        elif path.exists():
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))

    for cache in list(BACKEND.rglob("__pycache__")) + list(FRONTEND.rglob("__pycache__")):
        if "node_modules" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)

    if removed:
        ok("已清理：\n  " + "\n  ".join(removed))
        info("（.msh 是可再生缓存，下次划分网格时会重建）")
    else:
        info("没有需要清理的内容")
    return 0


# ------------------------------------------------------------------ 验证逻辑

def _health_ok() -> bool:
    try:
        _http_json("GET", f"{API_BASE}/")
        return True
    except Exception:
        return False


def _http_json(method: str, url: str, payload: Any = None, timeout: float = 120.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _http_status(method: str, url: str, payload: Any = None, timeout: float = 120.0) -> int:
    """
    发一个请求并**返回状态码**（成功与失败都返回），用于断言错误码。

    与 `_http_json` 的区别：`_http_json` 把非 2xx 当异常抛出——那对"验证正常流程"
    很方便，但要断言"这个请求应当被拒绝"就必须能拿到 4xx/5xx 本身。
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def _face_by_normal(faces, axis: int, sign: int):
    """按真实法向挑面——不能假设 face id 1..6 就是 ±X/±Y/±Z。"""
    for face in faces:
        normal = face.get("normal")
        if normal and normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


def _check_result_shader_contract() -> tuple[bool, str]:
    """
    着色器契约：结果云图必须**真的**显示变形。

    这道检查针对一个真实发生过的、静态检查发现不了的缺陷：顶点着色器里声明了
    ``uniform float deformationScale``，但 ``deformedPosition`` 被赋成了
    ``position``——云图永远画的是未变形的几何。它不报错、不影响任何数值，
    只在"求解后看图"时才暴露为"看不出变形"。

    检查方式：直接读取 GLSL 源码文本，断言
      * 位移属性存在；
      * 放大系数 uniform 存在；
      * ``deformedPosition`` 的赋值**同时**引用了二者；
      * 并且**不是**恒等赋值。
    """
    shader_path = FRONTEND / "components" / "resultShader.ts"
    if not shader_path.exists():
        return False, f"缺少 {shader_path.relative_to(ROOT)}"

    source = shader_path.read_text(encoding="utf-8")
    # 先去掉块注释：这个文件的文档注释里**故意**保留了出错前的反例片段
    # （`vec3 deformedPosition = position;`）作为说明，不剥掉就会匹配到它。
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    problems: list[str] = []

    if "attribute vec3 displacement" not in code:
        problems.append("顶点着色器没有声明 attribute vec3 displacement")
    if "uniform float deformationScale" not in code:
        problems.append("顶点着色器没有声明 uniform float deformationScale")

    matches = re.findall(r"vec3\s+deformedPosition\s*=\s*([^;]+);", code)
    if not matches:
        problems.append("顶点着色器没有计算 deformedPosition")
    for expression in matches:
        if "displacement" not in expression:
            problems.append(f"deformedPosition 没有使用位移场：{expression.strip()}")
        if "deformationScale" not in expression:
            problems.append(f"deformedPosition 没有应用放大系数：{expression.strip()}")
        if expression.strip() == "position":
            problems.append("deformedPosition 被赋成了 position（变形不显示，历史缺陷）")

    return (not problems), "；".join(problems) if problems else "位移属性与放大系数都参与了顶点计算"


def _run_node_module_selftest(
    node: str,
    script: str,
    env_extra: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    在 `frontend/utils/` 下用 node 执行一段断言脚本（脚本自己 import 前端的 `.ts`）。

    node >= 23 支持类型擦除，可以 `import './xxx.ts'`，因此前端算法不必在 Python 里
    重写一遍——重写一遍等于测试了一个"副本"，那种测试证明不了发布代码是对的。

    约定：脚本成功时打印 `OK` 并以 0 退出；失败时把原因写进 stderr 并以 1 退出。
    """
    environment = dict(os.environ)
    if env_extra:
        environment.update(env_extra)

    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=str(FRONTEND / "utils"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )

    if completed.returncode == 0 and "OK" in (completed.stdout or ""):
        return True, "断言全部通过"
    lines = (completed.stderr or completed.stdout or "").strip().splitlines()
    return False, (lines[-1] if lines else f"exit={completed.returncode}")


#: 用 node 直接执行前端纯函数模块并断言其行为。
_DEFORMATION_SELFTEST = r"""
import { computeDeformationScale, displacementMagnitudes, flattenDisplacements,
         hasDisplacementField, modelSpanOf,
         TARGET_DEFORMATION_FRACTION, MAX_DEFORMATION_SCALE } from './deformation.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 1) 定义本身：放大后的位移应等于"模型尺度的固定比例"。
//    跨尺寸/跨量级都要成立 —— 这正是它与旧的写死常量（0.5 个坐标单位的区别：
//    那个常量在小零件上太小、在大零件上太大）。
//    注意要比较**相对幅度** d*scale/span，而不是绝对幅度 d*scale：
//    模型尺寸不同，画面上该有的绝对变形量本来就不同。
for (const [span, d] of [[100, 0.001], [100, 0.02], [100, 0.5],
                         [1000, 0.1], [10, 0.001], [0.01, 1e-7]]) {
  const scale = computeDeformationScale(d, span);
  const relative = (d * scale) / span;
  check(`放大后相对幅度 span=${span} d=${d}`,
        Math.abs(relative - TARGET_DEFORMATION_FRACTION) < 1e-12,
        `${relative} vs ${TARGET_DEFORMATION_FRACTION}`);
}

// 3) 退化输入必须静止而不是发散（NaN/Infinity 会把整个画面画没）
const span = 100;
check('位移为 0 时系数为 1', computeDeformationScale(0, span) === 1);
check('位移缺失时系数为 1', computeDeformationScale(undefined, span) === 1);
check('模型尺度为 0 时系数为 1', computeDeformationScale(0.1, 0) === 1);
check('NaN 输入不产生 NaN 输出', Number.isFinite(computeDeformationScale(NaN, span)));
check('极小位移被上限截断',
      computeDeformationScale(1e-12, span) === MAX_DEFORMATION_SCALE);

// 4) 顶点属性长度必须严格等于 3 * 节点数
check('刚好匹配', flattenDisplacements([[1, 2, 3], [4, 5, 6]], 2).length === 6);
check('数据不足时补零', flattenDisplacements([[1, 2, 3]], 3).length === 9
      && flattenDisplacements([[1, 2, 3]], 3)[8] === 0);
check('数据过多时截断', flattenDisplacements([[1,2,3],[4,5,6],[7,8,9]], 1).length === 3);
check('没有数据也返回全长零数组', flattenDisplacements(undefined, 4).length === 12);
check('节点数为 0 时为空数组', flattenDisplacements([[1,2,3]], 0).length === 0);

// 5) 模型尺度：三个方向取最大值（与后端 compute_model_span 的定义一致）
check('modelSpanOf 取最大方向', modelSpanOf([[0,0,0],[1,2,0.5]]) === 2);
check('空输入返回 0', modelSpanOf([]) === 0 && modelSpanOf(null) === 0);

// 6) 位移模长与"是否有位移场"
const magnitudes = displacementMagnitudes([[3, 4, 0], [0, 0, 0]]);
check('位移模长', magnitudes[0] === 5 && magnitudes[1] === 0, JSON.stringify(magnitudes));
check('位移场存在性', hasDisplacementField([[1,2,3]], 1) && !hasDisplacementField([[1,2,3]], 2));

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_deformation_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/deformation.ts` 里的纯函数并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _DEFORMATION_SELFTEST)
    return ok, ("放大系数 / 属性长度 / 退化输入 均符合断言" if ok else detail)


#: 模态阶次列表 / 频率格式化 / 振型取场的断言（纯函数，喂构造数据）。
_MODAL_MODES_SELFTEST = r"""
import { MODE_LEGEND_TITLE, MODE_LEGEND_UNIT, UNKNOWN_FREQUENCY_LABEL,
         buildModeList, clampModeIndex, formatFrequency, modeDisplayField,
         modeHint } from './modalModes.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 1) 频率格式化：分档是为了让 0 Hz 的刚体模态与几十 kHz 的弹性模态在同一张
//    列表里都可读。无效值必须显示为占位符，**不能冒充 0 Hz**。
for (const [input, expected] of [
  [0, '0 Hz'], [1e-9, '0 Hz'], [0.5, '0.50 Hz'], [999.994, '999.99 Hz'],
  [1261.8862, '1.26 kHz'], [55747.3, '55.75 kHz'], [999999, '1000.00 kHz'],
  [2.5e6, '2.500 MHz'],
  [NaN, UNKNOWN_FREQUENCY_LABEL], [-1, UNKNOWN_FREQUENCY_LABEL],
  [undefined, UNKNOWN_FREQUENCY_LABEL], [null, UNKNOWN_FREQUENCY_LABEL],
]) {
  const got = formatFrequency(input);
  check(`formatFrequency(${input})`, got === expected, `${got} 应为 ${expected}`);
}

// 2) 阶次列表：order 从 1 起（给用户看），index 从 0 起（回传给父组件）
const freqs = [0, 0, 0, 0, 0, 0, 55747.3, 62310.5];
const modes = buildModeList(freqs, 6);
check('模态列表长度', modes.length === 8, String(modes.length));
check('阶次从 1 起', modes[0].order === 1 && modes[7].order === 8);
check('下标从 0 起', modes[0].index === 0 && modes[7].index === 7);
check('前 6 阶标记为刚体模态',
      modes.slice(0, 6).every(m => m.isRigidBody) && !modes[6].isRigidBody);
check('刚体模态显示为 0 Hz', modes[0].label === '0 Hz', modes[0].label);
check('空输入返回空列表', buildModeList([]).length === 0
      && buildModeList(null).length === 0 && buildModeList(undefined).length === 0);
check('rigidBodyModes 超过频率个数时不越界',
      buildModeList([1, 2], 99).every(m => m.isRigidBody));

// 3) 序号夹取：没有模态时返回 -1（UI 据此不渲染面板）
check('无模态时返回 -1', clampModeIndex(0, 0) === -1);
check('越界夹到最后一阶', clampModeIndex(9, 3) === 2);
check('负数夹到第一阶', clampModeIndex(-5, 3) === 0);
check('NaN 回落到第一阶', clampModeIndex(NaN, 3) === 0);
check('小数向下取整', clampModeIndex(1.9, 3) === 1);

// 4) 振型取场。后端把振型归一化到"最大节点位移 = 1"，所以着色场是**相对量**。
const shape = [[0, 0, 0], [3, 4, 0], [1, 0, 0]];
const field = modeDisplayField([shape], 0, 3);
check('振型取场非空', field !== null);
check('位移场逐点等于输入', JSON.stringify(field.displacements) === JSON.stringify(shape));
check('着色场为位移模长', field.scalarField[1] === 5, JSON.stringify(field.scalarField));
check('最大位移从数据算出', field.maxDisplacement === 5, String(field.maxDisplacement));

// 归一化后 maxDisplacement 应为 1；**放大 k 倍则最大位移也放大 k 倍**，
// 这样 utils/deformation.ts 的放大系数会自动保持不变 → 画面上的变形量不变。
const normalized = [[0, 0, 0], [1, 0, 0]];
check('归一化振型的最大位移为 1',
      modeDisplayField([normalized], 0, 2).maxDisplacement === 1);
const scaled = [[0, 0, 0], [1000, 0, 0]];
check('振型整体放大时最大位移同步放大',
      modeDisplayField([scaled], 0, 2).maxDisplacement === 1000);

// 数据对不上时必须返回 null —— 错位的云图比不显示更糟（用户会当真）
check('节点数不匹配返回 null', modeDisplayField([shape], 0, 5) === null);
check('没有振型返回 null', modeDisplayField(undefined, 0, 3) === null);
check('节点数为 0 返回 null', modeDisplayField([shape], 0, 0) === null);
check('越界下标夹到最后一阶而不是 null', modeDisplayField([shape, shape], 9, 3) !== null);

// 5) 提示文案：刚体模态是**正确**结果，但必须解释清楚，否则用户以为求解器坏了
const rigidHint = modeHint(modes[0], 6);
check('刚体模态有解释', typeof rigidHint === 'string' && rigidHint.includes('刚体'), String(rigidHint));
const elasticHint = modeHint(modes[6], 6);
check('弹性模态提示前几阶是刚体',
      typeof elasticHint === 'string' && elasticHint.includes('6'), String(elasticHint));
check('没有刚体模态时不提示', modeHint(modes[6], 0) === null);
check('没有选中阶次时不提示', modeHint(null, 6) === null);

// 6) 图例必须标明振型是**无量纲相对量**，否则用户会把颜色读成真实位移（米）
check('振型图例标题', MODE_LEGEND_TITLE.includes('振型'), MODE_LEGEND_TITLE);
check('振型图例单位标明无量纲', MODE_LEGEND_UNIT.includes('无量纲'), MODE_LEGEND_UNIT);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


#: 把**后端真实返回**的模态结果喂给前端的取场逻辑。
#: 这一步专门用来挡字段名漂移：后端的 mode_shapes / frequencies 一旦改名，
#: 界面只会"静默地不显示振型"，不会有任何报错。
_MODAL_DISPLAY_CHAIN_SELFTEST = r"""
import { readFileSync } from 'node:fs';
import { buildModeList, formatFrequency, modeDisplayField,
         UNKNOWN_FREQUENCY_LABEL } from './modalModes.ts';

const payload = JSON.parse(readFileSync(process.env.SIMCLOUD_MODAL_PAYLOAD, 'utf8'));

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const frequencies = payload.frequencies;
const shapes = payload.mode_shapes;
const nodeCount = payload.nodes;

check('后端返回了 frequencies', Array.isArray(frequencies) && frequencies.length > 0);
check('后端返回了 mode_shapes', Array.isArray(shapes) && shapes.length > 0);
check('频率与振型数量一致', frequencies.length === shapes.length,
      `${frequencies.length} vs ${shapes.length}`);
check('每阶振型长度等于网格节点数',
      shapes.every(s => s.length === nodeCount),
      `nodes=${nodeCount}, 各阶长度=${shapes.map(s => s.length).join(',')}`);

// Workbench 求解完默认显示第 1 阶（handleSelectMode/handleSolve 里的 index 0）
const first = modeDisplayField(shapes, 0, nodeCount);
check('默认阶次可取场（否则界面点了求解也看不到振型）', first !== null);
if (first) {
  check('振型已归一化 ⇒ 最大位移 = 1',
        Math.abs(first.maxDisplacement - 1) < 1e-9, String(first.maxDisplacement));
  check('着色场长度等于节点数', first.scalarField.length === nodeCount);
  check('着色场非负', first.scalarField.every(v => v >= 0));
}
// 切到最后一阶也必须能取场
check('最后一阶可取场',
      modeDisplayField(shapes, shapes.length - 1, nodeCount) !== null);
check('频率可正常格式化',
      formatFrequency(frequencies[0]) !== UNKNOWN_FREQUENCY_LABEL,
      formatFrequency(frequencies[0]));

// 阶次列表（界面右侧面板渲染的就是它）
const entries = buildModeList(frequencies, payload.rigid_body_modes || 0);
check('阶次列表长度与频率数一致', entries.length === frequencies.length);
check('刚体模态个数与后端一致',
      entries.filter(e => e.isRigidBody).length === (payload.rigid_body_modes || 0),
      `前端 ${entries.filter(e => e.isRigidBody).length} vs 后端 ${payload.rigid_body_modes}`);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_modal_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/modalModes.ts` 里的纯函数并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _MODAL_MODES_SELFTEST)
    return ok, ("阶次列表 / 频率格式化 / 振型取场 均符合断言" if ok else detail)


#: 项目列表的"接口 → 界面"映射断言。
#: 重点锁住一个真实崩溃：后端 `createdAt` 是 ISO **字符串**，而 `Project.createdAt`
#: 声明为 `Date`。旧代码直接 `.toLocaleDateString()`，接上真实接口就抛
#: TypeError —— 断言里显式证明了旧写法会炸、新写法不会。
_PROJECTS_API_SELFTEST = r"""
import { SIMULATION_TYPES, describeProjectError, formatCreatedAt,
         parseTimestamp, toProject, toProjectList } from './projectsApi.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const record = {
  id: 'abc123', title: '悬臂梁', description: '10×10×100',
  simulationType: 'FEA', isPrivate: false,
  createdAt: '2026-03-18T00:00:00+00:00', updatedAt: '2026-03-18T00:00:00+00:00',
};

// 1) 映射：时间戳必须变成 Date 对象，界面其余部分才能放心用
const project = toProject(record);
check('映射非空', project !== null);
check('createdAt 转成 Date', project.createdAt instanceof Date,
      typeof project.createdAt);
check('createdAt 值正确', project.createdAt.toISOString() === '2026-03-18T00:00:00.000Z',
      project.createdAt.toISOString());
check('simulationType 保留', project.simulationType === 'FEA');
check('isPrivate 保留为 false', project.isPrivate === false);

// 2) 回归：**字符串没有 toLocaleDateString** —— 旧代码就是这么崩的
check('字符串不是 Date（旧写法必然抛错）',
      typeof '2026-03-18T00:00:00+00:00'.toLocaleDateString === 'undefined');
check('工具函数能吃字符串', formatCreatedAt(record.createdAt) !== '—',
      formatCreatedAt(record.createdAt));
check('工具函数能吃 Date', formatCreatedAt(new Date('2026-03-18T00:00:00Z')) !== '—');

// 3) 坏数据不能渲染出 Invalid Date / 空白卡片
check('非法时间戳显示占位符', formatCreatedAt('garbage') === '—');
check('缺失时间戳显示占位符', formatCreatedAt(undefined) === '—'
      && formatCreatedAt(null) === '—');
check('非法时间解析为 null', parseTimestamp('not-a-date') === null
      && parseTimestamp(null) === null);
check('缺 id 的记录被丢弃', toProject({ title: 'x' }) === null);
check('缺 title 的记录被丢弃', toProject({ id: 'x' }) === null);
check('null 记录被丢弃', toProject(null) === null && toProject(undefined) === null);
check('未知 simulationType 回落 General',
      toProject({ id: 'x', title: 't', simulationType: 'CFD2' }).simulationType === 'General');
check('缺 description 回落空串', toProject({ id: 'x', title: 't' }).description === '');

// 4) 列表映射：坏记录被过滤，而不是让整个列表渲染失败
check('非数组返回空列表', toProjectList('x').length === 0
      && toProjectList(null).length === 0 && toProjectList(undefined).length === 0);
check('坏记录被过滤', toProjectList([record, null, {}, { id: 'y' }]).length === 1);
check('好记录全部保留', toProjectList([record, { id: 'y', title: 'B' }]).length === 2);

// 5) 后端可选值只有这四个（与 backend/project_store.py 的 SIMULATION_TYPES 一致）
check('simulationType 取值表', SIMULATION_TYPES.join(',') === 'CFD,FEA,Thermal,General',
      SIMULATION_TYPES.join(','));

// 6) 错误翻译：不同原因必须给不同的话，而不是笼统的 "出错了"
const network = describeProjectError({ code: 'ERR_NETWORK' });
check('网络错误提到后端/启动', network.includes('后端'), network);
const refused = describeProjectError({ code: 'ECONNREFUSED' });
check('连接被拒也提到后端', refused.includes('后端'), refused);
const notFound = describeProjectError({ response: { status: 404 } });
check('404 说明项目不存在', notFound.includes('不存在'), notFound);
const invalid = describeProjectError({ response: { status: 422 } });
check('422 说明请求不合法', invalid.includes('不合法'), invalid);
const badRequest = describeProjectError({
  response: { status: 400, data: { detail: '项目名称不能为空' } } });
check('400 透出后端 detail', badRequest.includes('项目名称不能为空'), badRequest);
const serverError = describeProjectError({ response: { status: 500 } });
check('5xx 标出状态码', serverError.includes('500'), serverError);
check('普通 Error 透出 message',
      describeProjectError(new Error('boom')) === 'boom');
check('未知输入有兜底', describeProjectError(null) === '未知错误。'
      && describeProjectError(undefined) === '未知错误。');

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_projects_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/projectsApi.ts` 里的纯函数并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _PROJECTS_API_SELFTEST)
    return ok, ("记录映射 / 时间戳解析 / 错误翻译 均符合断言" if ok else detail)


def _check_modal_display_chain(node: str, payload: dict) -> tuple[bool, str]:
    """
    把后端真实返回的模态结果喂给前端的取场逻辑。

    比"在前端造一份假数据"强的地方：后端字段一旦改名（`mode_shapes` → `modeShapes`），
    界面只会**静默地不显示振型**，不报错、不影响任何数值——只有拿真实响应去跑
    前端的解析逻辑才能发现。
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        path = handle.name

    try:
        ok, detail = _run_node_module_selftest(
            node,
            _MODAL_DISPLAY_CHAIN_SELFTEST,
            {"SIMCLOUD_MODAL_PAYLOAD": path},
        )
    finally:
        os.unlink(path)

    return ok, (f"{payload.get('nodes', 0)} 节点 × {len(payload.get('frequencies') or [])} 阶，"
                f"取场与归一化均通过" if ok else detail)


def task_verify(args: argparse.Namespace) -> int:
    """端到端验证 + 物理校准（当前 24 项，见 docs/01 的"三层验证"）。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, passed, detail))
        marker = _c("PASS", "green") if passed else _c("FAIL", "red")
        print(f"  [{marker}] {name:<42} {detail}")

    # node 在后面的"前端契约"小节里还要用（把真实响应喂给前端的取场逻辑），
    # 因此在这里统一探测一次。
    node_bin = shutil.which("node")

    if not args.skip_frontend:
        info("=== 1/4 前端类型检查 ===")
        if node_bin:
            code = subprocess.run(
                [node_bin, "node_modules/typescript/bin/tsc", "--noEmit"],
                cwd=str(FRONTEND),
            ).returncode
            check("TypeScript 类型检查", code == 0, f"exit={code}")

            # 结果云图的两件事都是"不看图就发现不了"的，所以这里做机器检查：
            # 1) 着色器是否真的把位移场用上了（历史缺陷：deformedPosition = position）；
            # 2) 变形放大系数这个纯函数的行为（用 node 直接跑前端的 .ts）
            passed, detail = _check_result_shader_contract()
            check("结果云图显示变形（着色器契约）", passed, detail)

            passed, detail = _check_frontend_deformation_math(node_bin)
            check("变形放大系数（node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_modal_math(node_bin)
            check("模态阶次与频率显示（node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_projects_math(node_bin)
            check("项目记录映射与错误翻译（node 执行前端纯函数）", passed, detail)
        else:
            check("TypeScript 类型检查", False, "未找到 node")

    info("\n=== 2/4 后端可达性 ===")
    try:
        _http_json("GET", f"{API_BASE}/")
        check("GET /", True, "Simulation Backend is running")
        reachable = True
    except Exception as exc:
        check("GET /", False, f"{type(exc).__name__}: 后端未启动？先跑 python tools/tasks.py dev")
        reachable = False

    if reachable:
        info("\n=== 3/4 API 冒烟测试 ===")
        try:
            materials = _http_json("GET", f"{API_BASE}/api/materials")
            check(
                "材料库",
                bool(materials) and materials[0].get("type") is not None,
                f"{len(materials)} 种，type={materials[0].get('type')}",
            )

            metadata = _http_json("GET", f"{API_BASE}/api/geometry/test_part.step/metadata")
            faces = metadata["faces"]
            with_normal = sum(1 for f in faces if f.get("normal"))
            with_area = sum(1 for f in faces if (f.get("area") or 0) > 0)
            check("B-Rep 面数量", len(faces) >= 7, f"{len(faces)} 个面")
            check("面法向已提取", with_normal == len(faces), f"{with_normal}/{len(faces)}")
            check("面面积已提取", with_area == len(faces), f"{with_area}/{len(faces)}")

            cylinder = next((f for f in faces if f.get("type") == "Cylinder"), None)
            if cylinder:
                import math

                expected = 2 * math.pi * 3 * 10
                error = abs(cylinder["area"] - expected) / expected
                check(
                    "圆柱面面积解析对照",
                    error < 0.01,
                    f"calc={cylinder['area']:.2f} expect={expected:.2f}",
                )
        except Exception as exc:
            check("API 冒烟测试", False, f"{type(exc).__name__}: {exc}")

        info("\n=== 4/4 求解器物理校准 ===")
        try:
            mesh = _http_json(
                "POST", f"{API_BASE}/api/generate-mesh?filename=test_part.step&mesh_size=1.2"
            )
            check(
                "网格生成（带孔方块）",
                len(mesh["elements"]) > 0,
                f"{len(mesh['nodes'])} 节点 / {len(mesh['elements'])} 单元",
            )

            minus_x = _face_by_normal(mesh["faces"], 0, -1)
            plus_x = _face_by_normal(mesh["faces"], 0, 1)
            body = {
                "geometry_filename": "test_part.step",
                "material_id": "structural_steel",
                "boundary_conditions": [
                    {"id": "fix", "name": "fixed", "type": "fixed",
                     "applicationType": "face", "entityIndex": minus_x["id"]},
                    {"id": "pull", "name": "pull", "type": "force",
                     "applicationType": "face", "entityIndex": plus_x["id"],
                     "force": {"x": 1000.0, "y": 0.0, "z": 0.0}},
                ],
                "faces": mesh["faces"],
            }
            result = _http_json("POST", f"{API_BASE}/api/solve", body)
            check("求解返回", result.get("status") == "solved", f"status={result.get('status')}")

            total_fx = sum(force[0] for force in result["reaction_forces"].values())
            check(
                "支反力与载荷守恒",
                abs(total_fx + 1000.0) < 1.0,
                f"sum_x={total_fx:.3f} (应为 -1000)",
            )

            stresses = result["stresses"]
            nan_count = sum(1 for value in stresses if value != value)
            check(
                "应力无 NaN",
                nan_count == 0,
                f"NaN={nan_count}, max={result['max_stress']:.2f}",
            )

            # 前端显示变形要用到这四个字段（见 Scene3D / utils/deformation.ts）。
            # 后端字段一旦改名，前端只会静默地"不显示变形"或算出 NaN 放大系数，
            # 因此在这里把字段契约固定下来。
            display_fields = ["nodes", "elements", "displacements", "max_displacement"]
            missing = [
                field for field in display_fields
                if field not in result and field not in mesh
            ]
            check(
                "变形显示所需字段齐全",
                not missing,
                f"缺失 {missing}" if missing else "nodes/elements/displacements/max_displacement",
            )

            # 立方体单轴拉伸：与解析解 FL/AE 对照（必须按真实法向挑面）
            cube = _http_json(
                "POST", f"{API_BASE}/api/generate-mesh?filename=default_cube.step&mesh_size=1.5"
            )
            cube_minus = _face_by_normal(cube["faces"], 0, -1)
            cube_plus = _face_by_normal(cube["faces"], 0, 1)
            cube_body = {
                "geometry_filename": "default_cube.step",
                "material_id": "structural_steel",
                "boundary_conditions": [
                    {"id": "fix", "name": "fixed", "type": "fixed",
                     "applicationType": "face", "entityIndex": cube_minus["id"]},
                    {"id": "pull", "name": "pull", "type": "force",
                     "applicationType": "face", "entityIndex": cube_plus["id"],
                     "force": {"x": 1000.0, "y": 0.0, "z": 0.0}},
                ],
                "faces": cube["faces"],
            }
            cube_result = _http_json("POST", f"{API_BASE}/api/solve", cube_body)

            best_index, best_distance = -1, float("inf")
            for index, node in enumerate(cube["nodes"]):
                distance = sum((node[i] - (5.0 if i == 0 else 0.0)) ** 2 for i in range(3))
                if distance < best_distance:
                    best_distance, best_index = distance, index

            ux = cube_result["displacements"][best_index][0]
            analytic = 1000.0 * 10.0 / (100.0 * 2.0e11)
            ratio = ux / analytic
            check(
                "立方体拉伸 vs 解析解 FL/AE",
                0.5 < ratio < 1.05,
                f"ratio={ratio:.3f} (期望 0.5~1.0)",
            )

            # 单位制：同一份网格按 mm 解释时，长度缩小 1000 倍 ⇒ 面积缩小 1e6 倍
            # ⇒ 应力放大 1e6 倍（位移放大 1000 倍）。这是端到端的单位换算校验。
            cube_mm = _http_json("POST", f"{API_BASE}/api/solve", dict(cube_body, length_unit="mm"))
            stress_ratio = cube_mm["max_stress"] / cube_result["max_stress"]
            check(
                "单位换算 (mm vs m：应力 ×1000²)",
                abs(stress_ratio - 1e6) < 1e6 * 1e-6,
                f"ratio={stress_ratio:.1f} (期望 1000000)",
            )
            check(
                "结果单位声明为 SI",
                cube_mm.get("units", {}).get("stress") == "Pa"
                and cube_mm.get("length_unit") == "mm",
                f"units={cube_mm.get('units')}, length_unit={cube_mm.get('length_unit')}",
            )

            # 异步任务：提交 -> 轮询 -> 结果必须与同步接口一致
            # （大模型不会再让请求超时；gmsh 非线程安全，所以任务在单线程里排队）
            submitted = _http_json(
                "POST",
                f"{API_BASE}/api/jobs/generate-mesh",
                {"filename": "test_part.step", "mesh_size": 1.2},
            )
            job_payload: dict = {}
            for _ in range(120):
                time.sleep(0.5)
                job_payload = _http_json("GET", f"{API_BASE}/api/jobs/{submitted['job_id']}")
                if job_payload.get("status") in ("succeeded", "failed"):
                    break

            job_result = job_payload.get("result") or {}
            check(
                "异步任务（提交→轮询→完成）",
                job_payload.get("status") == "succeeded",
                f"status={job_payload.get('status')}",
            )
            check(
                "异步结果与同步接口一致",
                len(job_result.get("nodes", [])) == len(mesh["nodes"]),
                f"async={len(job_result.get('nodes', []))} sync={len(mesh['nodes'])}",
            )

            # 稳态热传导：立方体两端定温、其余面绝热 ⇒ 温度精确线性、q = k·ΔT/L
            thermal_body = {
                "geometry_filename": "default_cube.step",
                "material_id": "structural_steel",
                "length_unit": "m",
                "faces": cube["faces"],
                "boundary_conditions": [
                    {"id": "cold", "name": "冷端", "type": "temperature",
                     "applicationType": "face", "entityIndex": cube_minus["id"],
                     "temperature": 273.15},
                    {"id": "hot", "name": "热端", "type": "temperature",
                     "applicationType": "face", "entityIndex": cube_plus["id"],
                     "temperature": 373.15},
                ],
            }
            thermal = _http_json("POST", f"{API_BASE}/api/thermal/solve", thermal_body)
            expected_flux = 50.0 * (373.15 - 273.15) / 10.0   # k=50, L=10 m
            check(
                "稳态热传导 vs 解析解 q=kΔT/L",
                abs(thermal["max_heat_flux"] - expected_flux) < expected_flux * 1e-4,
                f"q={thermal['max_heat_flux']:.6g} expect={expected_flux:.6g} W/m^2",
            )
            check(
                "热传导温度边界精确",
                abs(thermal["min_temperature"] - 273.15) < 1e-6
                and abs(thermal["max_temperature"] - 373.15) < 1e-6,
                f"T∈[{thermal['min_temperature']:.2f}, {thermal['max_temperature']:.2f}] K",
            )

            # 前端契约：热分析走 /api/jobs/thermal，温度由 °C 换算成 K 再提交，
            # 长度单位用前端默认的 mm。这一步专门验证「UI 实际发出的请求」能不能跑通。
            frontend_thermal = {
                "geometry_filename": "default_cube.step",
                "material_id": "structural_steel",
                "length_unit": "mm",
                "faces": cube["faces"],
                "boundary_conditions": [
                    {"id": "cold", "name": "temperature - face cold", "type": "temperature",
                     "applicationType": "face", "entityIndex": cube_minus["id"],
                     "color": "#aa66cc", "temperature": 25 + 273.15},
                    {"id": "hot", "name": "temperature - face hot", "type": "temperature",
                     "applicationType": "face", "entityIndex": cube_plus["id"],
                     "color": "#aa66cc", "temperature": 125 + 273.15},
                ],
            }
            submitted_thermal = _http_json(
                "POST", f"{API_BASE}/api/jobs/thermal", frontend_thermal
            )
            thermal_job: dict = {}
            for _ in range(120):
                time.sleep(0.5)
                thermal_job = _http_json(
                    "GET", f"{API_BASE}/api/jobs/{submitted_thermal['job_id']}"
                )
                if thermal_job.get("status") in ("succeeded", "failed"):
                    break
            thermal_result = thermal_job.get("result") or {}

            # mm ⇒ L = 0.01 m；ΔT = 100 K ⇒ q = 50 * 100 / 0.01 = 5e5 W/m²
            expected_frontend_flux = 50.0 * 100.0 / 0.01
            check(
                "热分析前端契约（°C→K + mm + 异步）",
                thermal_job.get("status") == "succeeded"
                and abs(thermal_result.get("max_heat_flux", 0.0) - expected_frontend_flux)
                < expected_frontend_flux * 1e-3,
                f"q={thermal_result.get('max_heat_flux')} expect={expected_frontend_flux}",
            )

            # 模态分析：这里刻意**不**去对某个"标准频率"下手（短粗立方体没有干净
            # 的闭式解），而是校验两条**精确关系**，它们都来自解析结论：
            #   1) 自由-自由结构恰好有 6 个刚体模态（3 平移 + 3 转动），频率为 0；
            #   2) ω ∝ √(E/ρ)/L ⇒ 同一份网格按 mm 与按 m 解释，频率相差**恰好 1000 倍**。
            def _solve_modal(unit, bcs, num_modes):
                submitted_modal = _http_json("POST", f"{API_BASE}/api/jobs/modal", {
                    "geometry_filename": "default_cube.step",
                    "material_id": "structural_steel",
                    "length_unit": unit,
                    "num_modes": num_modes,
                    "faces": cube["faces"],
                    "boundary_conditions": bcs,
                })
                for _ in range(240):
                    time.sleep(0.5)
                    payload = _http_json(
                        "GET", f"{API_BASE}/api/jobs/{submitted_modal['job_id']}"
                    )
                    if payload.get("status") in ("succeeded", "failed"):
                        return payload
                return {"status": "timeout"}

            free_free = _solve_modal("mm", [], 8)
            free_free_result = free_free.get("result") or {}
            check(
                "模态分析刚体模态（自由-自由 ⇒ 6 个零频）",
                free_free.get("status") == "succeeded"
                and free_free_result.get("rigid_body_modes") == 6,
                f"status={free_free.get('status')} "
                f"rigid={free_free_result.get('rigid_body_modes')}",
            )

            fixed_face = {
                "id": "fix", "name": "固定端", "type": "fixed",
                "applicationType": "face", "entityIndex": cube_minus["id"],
            }
            modal_mm = _solve_modal("mm", [fixed_face], 4)
            modal_m = _solve_modal("m", [fixed_face], 4)
            mm_freqs = (modal_mm.get("result") or {}).get("frequencies") or []
            m_freqs = (modal_m.get("result") or {}).get("frequencies") or []
            ratios = [
                mm / m for mm, m in zip(mm_freqs, m_freqs) if m > 0.0
            ]
            check(
                "模态分析单位缩放（mm vs m 频率 ×1000）",
                modal_mm.get("status") == "succeeded"
                and modal_m.get("status") == "succeeded"
                and len(ratios) == 4
                and all(abs(ratio - 1000.0) < 1e-6 for ratio in ratios),
                f"f1={mm_freqs[0]:.6g} Hz, 比值={ratios[:2]}"
                if ratios else f"mm={modal_mm.get('status')} m={modal_m.get('status')}",
            )

            # 模态前端契约：把**后端真实返回**的模态结果喂给界面实际使用的
            # 取场逻辑（frontend/utils/modalModes.ts）。这一步挡的是"字段名漂移"
            # ——后端把 mode_shapes 改名后，界面只会静默地不显示振型。
            if node_bin:
                passed, detail = _check_modal_display_chain(
                    node_bin,
                    {
                        "mode_shapes": (modal_mm.get("result") or {}).get("mode_shapes"),
                        "frequencies": mm_freqs,
                        "rigid_body_modes": (
                            modal_mm.get("result") or {}
                        ).get("rigid_body_modes", 0),
                        "nodes": len(cube["nodes"]),
                    },
                )
                check("模态分析前端契约（真实响应对接取场逻辑）", passed, detail)

                # 自由-自由那条结果里前 6 阶是刚体模态，正好用来验证界面
                # "前 N 阶标记为刚体"的渲染输入是否正确
                passed, detail = _check_modal_display_chain(
                    node_bin,
                    {
                        "mode_shapes": free_free_result.get("mode_shapes"),
                        "frequencies": free_free_result.get("frequencies") or [],
                        "rigid_body_modes": free_free_result.get("rigid_body_modes", 0),
                        "nodes": len(cube["nodes"]),
                    },
                )
                check("模态前端契约（自由-自由 ⇒ 6 阶刚体）", passed, detail)
        except Exception as exc:
            # 这个兜底捕获会把一整段物理校准变成"一项失败"，因此必须把栈打出来：
            # 只报一句异常摘要的话，排查时根本看不出是哪一行炸的
            # （本轮就吃过这个亏：TypeError 的摘要完全指不出位置）。
            import traceback

            traceback.print_exc()
            check("求解器物理校准", False, f"{type(exc).__name__}: {exc}")

    if reachable:
        info("\n=== 5/5 项目管理（持久化实体）===")
        # 在这之前项目只活在前端内存里：新建即丢、无法引用。这组检查走真实 HTTP，
        # 验证"项目是一个真正的后端实体"这条承诺：ID 由服务端生成、能被列出来、
        # 能改名、删掉之后确实 404。
        created_id: Optional[str] = None
        try:
            marker = f"verify-{uuid.uuid4().hex[:8]}"
            created = _http_json("POST", f"{API_BASE}/api/projects", {
                "title": f"契约检查 {marker}",
                "description": "由 tools/tasks.py verify 创建，跑完会删掉",
                "simulationType": "FEA",
                "isPrivate": True,
            })
            created_id = created.get("id")

            check(
                "创建项目返回服务端 ID 与时间戳",
                bool(created_id) and bool(created.get("createdAt"))
                and str(created.get("createdAt", "")).endswith("+00:00"),
                f"id={created_id} createdAt={created.get('createdAt')}",
            )

            listing = _http_json("GET", f"{API_BASE}/api/projects")
            ids = [item.get("id") for item in listing] if isinstance(listing, list) else []
            check(
                "新项目出现在列表最前",
                bool(ids) and ids[0] == created_id,
                f"{len(ids)} 个项目，第一个={ids[0] if ids else None}",
            )

            fetched = _http_json("GET", f"{API_BASE}/api/projects/{created_id}")
            check(
                "按 ID 取回同一项目",
                fetched.get("id") == created_id
                and fetched.get("title") == f"契约检查 {marker}",
                f"title={fetched.get('title')}",
            )

            renamed = _http_json(
                "PATCH", f"{API_BASE}/api/projects/{created_id}", {"title": "改名后"}
            )
            check(
                "PATCH 只改给定字段",
                renamed.get("title") == "改名后"
                and renamed.get("description") == "由 tools/tasks.py verify 创建，跑完会删掉",
                f"title={renamed.get('title')}",
            )

            check(
                "服务端拒绝客户端自选 ID",
                _http_status("POST", f"{API_BASE}/api/projects",
                             {"id": "my-own-id", "title": "x"}) == 422,
                "请求体里带 id 应为 422（否则客户端能覆盖别人的记录）",
            )
            check(
                "空标题被拒绝",
                _http_status("POST", f"{API_BASE}/api/projects", {"title": "   "}) == 422,
                "空标题应为 422",
            )

            _http_json("DELETE", f"{API_BASE}/api/projects/{created_id}")
            deleted_status = _http_status("GET", f"{API_BASE}/api/projects/{created_id}")
            check(
                "删除后确实不存在（404 而不是静默成功）",
                deleted_status == 404,
                f"GET 已删除项目 -> HTTP {deleted_status}",
            )
            created_id = None
        except Exception as exc:
            import traceback

            traceback.print_exc()
            check("项目管理 CRUD", False, f"{type(exc).__name__}: {exc}")
        finally:
            # 检查用的项目不要留在用户的数据库里
            if created_id:
                try:
                    _http_json("DELETE", f"{API_BASE}/api/projects/{created_id}")
                except Exception:  # noqa: BLE001 - 清理失败不该影响验证结论
                    pass

    failed = [name for name, passed, _ in checks if not passed]
    print()
    print(_c("=" * 52, "cyan"))
    if not failed:
        print(_c("  全部通过", "green"))
    else:
        print(_c(f"  {len(failed)} 项失败：{', '.join(failed)}", "red"))
    print(_c("=" * 52, "cyan"))
    return 1 if failed else 0


def task_doctor(args: argparse.Namespace) -> int:
    """检查本机开发环境是否齐备。"""
    info("=== 环境体检 ===")
    print(f"  Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  平台        : {sys.platform}")

    for name in ("node", "npm", "git"):
        location = shutil.which(name)
        print(f"  {name:<11} : {location or _c('未找到', 'red')}")

    python = venv_python()
    print(f"  后端 venv   : {python if python.exists() else _c('未创建（先跑 setup）', 'yellow')}")
    print(f"  backend/.env: {'存在' if (BACKEND / '.env').exists() else _c('缺失', 'yellow')}")
    print(f"  frontend/node_modules: {'存在' if (FRONTEND / 'node_modules').exists() else _c('缺失', 'yellow')}")

    reachable = _health_ok()
    print(f"  后端服务    : {'运行中' if reachable else '未运行'}")
    return 0


TASKS = {
    "setup": task_setup,
    "dev": task_dev,
    "stop": task_stop,
    "test": task_test,
    "verify": task_verify,
    "build": task_build,
    "clean": task_clean,
    "doctor": task_doctor,
}


def main(argv: Optional[list[str]] = None) -> int:
    _configure_streams()
    parser = argparse.ArgumentParser(
        prog="tasks.py",
        description="SimCloud AI 跨平台开发任务（Windows / Linux / macOS）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  python tools/tasks.py doctor\n  python tools/tasks.py test\n",
    )
    parser.add_argument("task", choices=sorted(TASKS), help="要执行的任务")
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="verify 时跳过前端类型检查（例如没装 node 的纯后端环境）",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="dev 时后台运行并立即返回（配合 stop 使用；适合脚本/CI）",
    )
    args = parser.parse_args(argv)
    return TASKS[args.task](args)


if __name__ == "__main__":
    raise SystemExit(main())
