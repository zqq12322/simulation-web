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


def _http_json(
    method: str,
    url: str,
    payload: Any = None,
    timeout: float = 120.0,
    headers: Optional[dict] = None,
):
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _http_status(
    method: str,
    url: str,
    payload: Any = None,
    timeout: float = 120.0,
    headers: Optional[dict] = None,
) -> int:
    """
    发一个请求并**返回状态码**（成功与失败都返回），用于断言错误码。

    与 `_http_json` 的区别：`_http_json` 把非 2xx 当异常抛出——那对"验证正常流程"
    很方便，但要断言"这个请求应当被拒绝"就必须能拿到 4xx/5xx 本身。
    """
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _http_download(url: str, headers: Optional[dict] = None) -> dict:
    """
    取一个文件并返回状态码、字节数与 Content-Type（**不解析正文**）。

    用于验证"下载端点真的返回了文件"，而不是只确认它返回了 200。
    """
    request_headers = {"Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return {
                "status": int(response.status),
                "bytes": len(body),
                "content_type": response.headers.get("Content-Type", ""),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": int(error.code),
            "bytes": 0,
            "content_type": error.headers.get("Content-Type", "") if error.headers else "",
        }


#: 一个极小的合法 STL（一个三角形），用于验证上传→下载的往返
_PROBE_STL = (
    b"solid probe\n"
    b"facet normal 0 0 1\n"
    b"  outer loop\n"
    b"    vertex 0 0 0\n"
    b"    vertex 1 0 0\n"
    b"    vertex 0 1 0\n"
    b"  endloop\n"
    b"endfacet\n"
    b"endsolid probe\n"
)


def _upload_probe_stl(headers: dict) -> str:
    """
    上传一个临时 STL 并返回它的文件名。

    为什么要真的上传：预览 STL 只在上传时生成，开发机的 uploads/ 里不一定有
    现成的文件；而且"上传→下载"这条往返本身就值得验证。
    """
    filename = f"verify_probe_{uuid.uuid4().hex[:8]}.stl"
    boundary = "----verify" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + _PROBE_STL + f"\r\n--{boundary}--\r\n".encode("utf-8")

    request = urllib.request.Request(
        f"{API_BASE}/api/upload-geometry",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            **headers,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("filename") or filename


def _remove_uploaded_file(filename: str) -> None:
    """删掉 verify 临时上传的文件（uploads/ 是共享目录，不能留垃圾）。"""
    try:
        candidate = (BACKEND / "uploads" / filename)
        if candidate.is_file():
            candidate.unlink()
    except Exception:  # noqa: BLE001 - 清理失败不该影响验证结论
        pass


def _project_db_path() -> Path:
    """开发数据库路径（与 `backend/config.py` 的 `SIMCLOUD_DB` 规则一致）。"""
    override = os.environ.get("SIMCLOUD_DB")
    if override:
        return Path(override)
    return BACKEND / "data" / "simcloud.db"


def _insert_legacy_project(project_id: str, title: str) -> None:
    """
    直接往库里插一条 ``owner_id IS NULL`` 的项目，**模拟"接上登录之前的数据库"**。

    为什么需要它：遗留项目只能这样造——HTTP 接口创建的项目一定有属主。
    这也是本轮最值得端到端验证的一条策略（可见但不可改、需显式认领），
    而它没法只靠接口构造出前置状态。
    """
    import sqlite3
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection = sqlite3.connect(str(_project_db_path()))
    try:
        with connection:
            connection.execute(
                "INSERT INTO projects (id, title, description, simulation_type,"
                " is_private, owner_id, created_at, updated_at)"
                " VALUES (?, ?, '', 'General', 1, NULL, ?, ?)",
                (project_id, title, timestamp, timestamp),
            )
    finally:
        connection.close()


def _delete_legacy_project(project_id: str) -> None:
    """清掉 verify 临时造的遗留项目（要按主键直删，因为它可能已被认领）。"""
    import sqlite3

    try:
        connection = sqlite3.connect(str(_project_db_path()))
        try:
            with connection:
                connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - 清理失败不该影响验证结论
        pass


def _login_or_register(username: str, password: str) -> dict:
    """
    拿到一个可用令牌：先尝试登录，失败（401）则注册——注册也会直接返回令牌。

    verify 用**固定的测试账号**，因此不会每次运行都往开发库里塞新用户。
    """
    try:
        return _http_json(
            "POST", f"{API_BASE}/api/auth/login",
            {"username": username, "password": password},
        )
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
    return _http_json(
        "POST", f"{API_BASE}/api/auth/register",
        {"username": username, "password": password},
    )


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
import { SIMULATION_TYPES, canEditProject, canManageProject, canModify,
         describePermissionNotice, describeProjectError, describeRole,
         formatCreatedAt, isUnowned, parseTimestamp, roleOf, toProject,
         toProjectList } from './projectsApi.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const record = {
  id: 'abc123', title: '悬臂梁', description: '10×10×100',
  simulationType: 'FEA', isPrivate: false, ownerId: 'user-1',
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
check('ownerId 保留', project.ownerId === 'user-1');

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

// ownerId：缺失与 null 都归一化成 null（后端用 owner_id IS NULL 表达遗留项目）
check('缺 ownerId 归一化为 null', toProject({ id: 'x', title: 't' }).ownerId === null);
check('ownerId 为 null 时保持 null',
      toProject({ id: 'x', title: 't', ownerId: null }).ownerId === null);
check('ownerId 非字符串时归一化为 null',
      toProject({ id: 'x', title: 't', ownerId: 42 }).ownerId === null);

// 无主项目：**可见但不可改**。这取代了"谁先注册谁自动得到"，
// 后者曾把开发者手工建的项目静默划给 verify 的测试账号。
check('识别无主项目', isUnowned({ ownerId: null }) && isUnowned({}) && isUnowned(null));
check('有主项目不算无主', !isUnowned({ ownerId: 'user-1' }));
check('自己的项目可改', canModify({ ownerId: 'user-1' }, 'user-1'));
check('别人的项目不可改', !canModify({ ownerId: 'user-2' }, 'user-1'));
check('无主项目不可改（必须先认领）', !canModify({ ownerId: null }, 'user-1'));
check('未登录时不可改', !canModify({ ownerId: 'user-1' }, null)
      && !canModify({ ownerId: 'user-1' }, undefined));

// 共享角色：界面判权限的口径必须与后端 ProjectStore.can_edit / can_manage 一致。
// 不一致的后果是"界面允许操作、后端 403"——用户会以为系统坏了。
check('角色归一化', roleOf({ role: 'editor' }) === 'editor'
      && roleOf({ role: 'nonsense' }) === null && roleOf(null) === null
      && roleOf({}) === null);
check('owner 可编辑可管理',
      canEditProject({ role: 'owner' }) && canManageProject({ role: 'owner' }));
check('editor 可编辑但不可管理',
      canEditProject({ role: 'editor' }) && !canManageProject({ role: 'editor' }));
check('viewer 既不可编辑也不可管理',
      !canEditProject({ role: 'viewer' }) && !canManageProject({ role: 'viewer' }));
check('未认领的无主项目不可编辑',
      !canEditProject({ role: 'unowned' }) && !canManageProject({ role: 'unowned' }));
check('没有角色等于没有权限',
      !canEditProject(null) && !canManageProject(undefined) && !canEditProject({}));
check('角色说明只对非本人显示', describeRole({ role: 'owner' }) === null
      && describeRole({ role: 'editor' }).includes('可编辑')
      && describeRole({ role: 'viewer' }).includes('只读')
      && describeRole({ role: 'unowned' }) === '未归属');
check('权限提示覆盖三种受限情况',
      describePermissionNotice({ role: 'editor' }).includes('不能改名')
      && describePermissionNotice({ role: 'viewer' }).includes('只读')
      && describePermissionNotice({ role: 'unowned' }).includes('认领')
      && describePermissionNotice({ role: 'owner' }) === null);

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


#: 认证工具的断言：令牌存取、字段映射、请求头、401 与"连不上"的区分。
_AUTH_API_SELFTEST = r"""
import { TOKEN_STORAGE_KEY, SESSION_EXPIRED_EVENT, authorizationHeader,
         clearStoredToken, currentAuthHeaders, defaultStorage,
         describeAuthError, describeOwner, errorStatus, isUnauthorized,
         notifySessionExpired, readStoredToken, toSession, toUser,
         writeStoredToken } from './authApi.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 用一个假存储，断言"读写清"三条路径。假存储可以模拟抛异常的环境
// （Safari 隐私模式、被策略禁用的存储），那正是组件里直接写 localStorage 会白屏的场景。
const makeStorage = () => {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    _dump: () => map,
  };
};

let storage = makeStorage();
check('初始没有令牌', readStoredToken(storage) === null);
check('写入令牌成功', writeStoredToken('tok-123', storage) === true);
check('读回令牌', readStoredToken(storage) === 'tok-123');
check('键名固定', storage._dump().has(TOKEN_STORAGE_KEY), TOKEN_STORAGE_KEY);
clearStoredToken(storage);
check('清除令牌', readStoredToken(storage) === null);
check('清除是幂等的', (clearStoredToken(storage), readStoredToken(storage) === null));

// 空白令牌不该被当成"已登录"
check('空白令牌视为未登录', readStoredToken(makeStorage()) === null);
const blank = makeStorage();
blank.setItem(TOKEN_STORAGE_KEY, '   ');
check('只有空格的令牌视为未登录', readStoredToken(blank) === null);
check('拒绝写入空令牌', writeStoredToken('', makeStorage()) === false);

// 存储不可用（构造器抛异常 / null）时，一律安全返回，绝不把异常抛给组件
const throwing = {
  getItem: () => { throw new Error('SecurityError'); },
  setItem: () => { throw new Error('QuotaExceededError'); },
  removeItem: () => { throw new Error('SecurityError'); },
};
check('读取失败的存储返回 null', readStoredToken(throwing) === null);
check('写入失败的存储返回 false（不抛）', writeStoredToken('t', throwing) === false);
check('清除失败的存储不抛', (clearStoredToken(throwing), true));
check('null 存储安全', readStoredToken(null) === null
      && writeStoredToken('t', null) === false && (clearStoredToken(null), true));

// 请求头：没有令牌时**不能**发 "Bearer undefined"
check('无令牌时不带 Authorization',
      authorizationHeader(null).Authorization === undefined
      && authorizationHeader(undefined).Authorization === undefined
      && authorizationHeader('').Authorization === undefined);
check('有令牌时带 Bearer',
      authorizationHeader('abc').Authorization === 'Bearer abc');
check('令牌两侧空白被去掉',
      authorizationHeader('  abc  ').Authorization === 'Bearer abc');
check('额外头被保留',
      authorizationHeader('abc', { 'Content-Type': 'application/json' })['Content-Type']
      === 'application/json');
check('默认带 Accept', authorizationHeader(null).Accept === 'application/json');

// 用户 / 会话映射
const userRecord = {
  id: 'u1', username: 'alice', displayName: 'Alice',
  createdAt: '2026-03-18T00:00:00+00:00',
};
check('用户映射', toUser(userRecord).username === 'alice');
check('缺 displayName 时回落到 username',
      toUser({ id: 'u1', username: 'bob' }).displayName === 'bob');
check('缺 id 或 username 返回 null',
      toUser({ username: 'bob' }) === null && toUser({ id: 'u1' }) === null
      && toUser(null) === null && toUser('nope') === null);

check('会话映射', (toSession({ token: 't', user: userRecord }) || {}).token === 't');
check('缺令牌的响应返回 null', toSession({ user: userRecord }) === null);
check('缺用户的响应返回 null', toSession({ token: 't' }) === null);
check('令牌全是空白返回 null', toSession({ token: '   ', user: userRecord }) === null);
check('垃圾输入返回 null', toSession(null) === null && toSession('x') === null);

// 401 与"连不上后端"必须区分：前者要重新登录，后者要先把服务起起来
check('识别 401', isUnauthorized({ response: { status: 401 } })
      && !isUnauthorized({ response: { status: 500 } })
      && !isUnauthorized({ code: 'ERR_NETWORK' }));
check('状态码提取', errorStatus({ response: { status: 403 } }) === 403
      && errorStatus({ code: 'ERR_NETWORK' }) === null
      && errorStatus(null) === null);

const unauthorized = describeAuthError({ response: { status: 401 } });
check('401 提示口令错误', unauthorized.includes('口令'), unauthorized);
const declaredDetail = describeAuthError({
  response: { status: 401, data: { detail: '登录已失效，请重新登录' } } });
check('401 透出后端 detail', declaredDetail.includes('登录已失效'), declaredDetail);
const offline = describeAuthError({ code: 'ERR_NETWORK' });
check('网络错误提到启动后端', offline.includes('后端'), offline);
const validation = describeAuthError({ response: { status: 422 } });
check('422 提到输入不合法', validation.includes('不合法'), validation);
check('未知输入有兜底', describeAuthError(null) === '未知错误。');

// 属主标记：让"归属"在界面上显式可见（含"无主"这种遗留状态）
check('自己的项目', describeOwner({ isPrivate: true, ownerId: 'u1' }, 'u1') === '我的项目');
check('自己的公开项目',
      describeOwner({ isPrivate: false, ownerId: 'u1' }, 'u1') === '我的项目（公开）');
check('他人的项目',
      describeOwner({ isPrivate: true, ownerId: 'u2' }, 'u1') === '他人的项目');
check('无主项目',
      describeOwner({ isPrivate: true, ownerId: null }, 'u1') === '未归属');

// currentAuthHeaders：从存储里读令牌，任何一处 axios 调用都能直接用
// （不必把 token 穿过整棵组件树）。没有存储时也必须安全返回。
check('无存储时 currentAuthHeaders 不抛且不带 Authorization',
      typeof currentAuthHeaders() === 'object'
      && currentAuthHeaders().Authorization === undefined);
check('currentAuthHeaders 透传额外头',
      currentAuthHeaders({ 'Content-Type': 'multipart/form-data' })['Content-Type']
      === 'multipart/form-data');

// 注入一个假的全局 localStorage，验证"有令牌时会带上"
globalThis.localStorage = makeStorage();
writeStoredToken('tok-from-storage');
check('currentAuthHeaders 自动带上存储里的令牌',
      currentAuthHeaders().Authorization === 'Bearer tok-from-storage',
      String(currentAuthHeaders().Authorization));
clearStoredToken();
check('登出后 currentAuthHeaders 不再带令牌',
      currentAuthHeaders().Authorization === undefined);
delete globalThis.localStorage;

// 会话失效事件：子组件（求解/材料/AI）拿不到 App 的状态，靠广播事件通知
check('会话失效事件名非空', typeof SESSION_EXPIRED_EVENT === 'string'
      && SESSION_EXPIRED_EVENT.length > 0, SESSION_EXPIRED_EVENT);
check('没有 window 时 notifySessionExpired 不抛',
      (notifySessionExpired('测试'), true));

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_auth_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/authApi.ts` 里的纯函数并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _AUTH_API_SELFTEST)
    return ok, ("令牌存取 / 请求头 / 错误区分 均符合断言" if ok else detail)


#: 项目配置（自动保存）的纯逻辑断言。
#: 重点锁三件事：组装只带该带的字段、**按键排序的稳定签名**（否则每次重渲染都会
#: 写一次库）、以及恢复时对不认识的内容必须丢弃并报告。
_PROJECT_SETUP_SELFTEST = r"""
import { SETUP_VERSION, buildSetupPayload, describeSaveStatus,
         describeSetupBadge, restoreSetup, setupSignature,
         stableStringify } from './projectSetup.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const bc = { id: 'bc1', name: '固定端', type: 'fixed',
             applicationType: 'face', entityIndex: 1, color: '#f00' };
const source = {
  modelName: 'test_part.step',
  selectedMaterial: { id: 'structural_steel', name: '结构钢' },
  boundaryConditions: [bc],
  meshSettings: { id: 'm1', meshSize: 1.5, status: 'meshed' },
  solverSettings: { id: 's1', solverType: 'structural', lengthUnit: 'mm' },
};

// 1) 组装：版本号、字段映射、深拷贝
const payload = buildSetupPayload(source);
check('版本号', payload.version === SETUP_VERSION);
check('几何文件名', payload.geometryFilename === 'test_part.step');
check('材料只取 id', payload.materialId === 'structural_steel');
check('边界条件条数', payload.boundaryConditions.length === 1);
check('求解设置带上', payload.solverSettings.lengthUnit === 'mm');

// 深拷贝：之后再改界面状态不该影响已经组装好的文档
source.boundaryConditions.push({ ...bc, id: 'bc2' });
source.meshSettings.meshSize = 99;
check('深拷贝边界条件', payload.boundaryConditions.length === 1,
      String(payload.boundaryConditions.length));
check('深拷贝网格设置', payload.meshSettings.meshSize === 1.5,
      String(payload.meshSettings.meshSize));

const empty = buildSetupPayload({ modelName: null, selectedMaterial: null,
  boundaryConditions: [], meshSettings: null, solverSettings: null });
check('空状态可组装', empty.geometryFilename === null
      && empty.materialId === null && empty.boundaryConditions.length === 0);

// 2) 稳定签名：键顺序不同必须得到同一个签名
check('stableStringify 对键排序',
      stableStringify({ b: 1, a: 2 }) === stableStringify({ a: 2, b: 1 }),
      stableStringify({ b: 1, a: 2 }));
check('stableStringify 递归排序',
      stableStringify({ x: { b: 1, a: 2 } }) === stableStringify({ x: { a: 2, b: 1 } }));
check('数组顺序仍然有意义',
      stableStringify([1, 2]) !== stableStringify([2, 1]));

const reordered = { version: 1,
  solverSettings: { lengthUnit: 'mm', id: 's1', solverType: 'structural' },
  meshSettings: { status: 'meshed', meshSize: 1.5, id: 'm1' },
  boundaryConditions: [bc], materialId: 'structural_steel',
  geometryFilename: 'test_part.step' };
check('签名与键顺序无关', setupSignature(payload) === setupSignature(reordered),
      setupSignature(payload) === setupSignature(reordered) ? '' : '键顺序不同却得到不同签名');
check('签名忽略 version',
      setupSignature({ ...payload, version: 99 }) === setupSignature(payload));
check('真正改动会改变签名',
      setupSignature({ ...payload, geometryFilename: 'other.step' })
      !== setupSignature(payload));
check('缺失配置的签名为空串',
      setupSignature(null) === '' && setupSignature(undefined) === '');

// 3) 恢复：认识的内容拿回来，不认识的内容丢弃并报告
const restored = restoreSetup(payload);
check('恢复几何', restored.geometryFilename === 'test_part.step');
check('恢复材料 id', restored.materialId === 'structural_steel');
check('恢复边界条件', restored.boundaryConditions.length === 1);
check('恢复网格设置', restored.meshSettings.meshSize === 1.5);
check('完好文档没有警告', restored.warnings.length === 0,
      JSON.stringify(restored.warnings));

const broken = restoreSetup({
  version: 99,
  geometryFilename: 123,
  materialId: null,
  boundaryConditions: 'not-an-array',
  meshSettings: [1, 2, 3],
  solverSettings: { ok: true },
});
check('版本不符要报告', broken.warnings.some(w => w.includes('99')),
      JSON.stringify(broken.warnings));
check('类型不对的字段被丢弃', broken.geometryFilename === null
      && broken.boundaryConditions.length === 0 && broken.meshSettings === null);
// 1 条版本警告 + 3 条字段警告（geometryFilename / boundaryConditions / meshSettings）
check('每一项问题都有说明', broken.warnings.length === 4,
      JSON.stringify(broken.warnings));
check('能认的还是认了', broken.solverSettings.ok === true);
check('空文档安全', restoreSetup(null).geometryFilename === null
      && restoreSetup(null).warnings.length === 0
      && restoreSetup(undefined).boundaryConditions.length === 0
      && restoreSetup('nonsense').warnings.length === 0);

// 4) 保存状态文案：error 绝不能显示成"已保存"
const failed = describeSaveStatus('error', { error: '后端不可用' });
check('失败文案不说已保存', !failed.includes('已保存'), failed);
check('失败文案带原因', failed.includes('后端不可用'), failed);
check('保存中文案', describeSaveStatus('saving').includes('保存'));
check('已保存文案', describeSaveStatus('saved',
      { savedAt: '2026-03-18T00:00:00+00:00' }).startsWith('已保存'));
check('时间戳非法时有兜底',
      describeSaveStatus('saved', { savedAt: 'garbage' }) === '已保存');
check('初始状态文案', describeSaveStatus('idle') === '未修改');

// 5) 仪表盘标记
check('已配置标记', describeSetupBadge(true) === '已配置');
check('空项目标记', describeSetupBadge(false) === '空项目'
      && describeSetupBadge(null) === '空项目');

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_project_setup_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/projectSetup.ts` 里的纯函数并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _PROJECT_SETUP_SELFTEST)
    return ok, ("组装 / 稳定签名 / 恢复校验 / 状态文案 均符合断言" if ok else detail)


#: 几何取用逻辑（带认证的 blob 加载）断言。
#: 这一段最值得机器验证的地方：**加载器不会带 Authorization 头**，
#: 所以"URL 拼得对不对、令牌有没有带上、各种失败怎么报"必须逐条钉住。
_MODEL_SOURCE_SELFTEST = r"""
import { ModelLoadError, describeModelError, fetchModelBlobUrl,
         geometryDownloadUrl, isSafeFilename,
         renderFilenameFor } from './modelSource.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 1) URL 与文件名规则
check('下载地址', geometryDownloadUrl('http://x:8000', 'a.stl')
      === 'http://x:8000/api/geometry/a.stl/download',
      geometryDownloadUrl('http://x:8000', 'a.stl'));
check('末尾斜杠不产生双斜杠', geometryDownloadUrl('http://x:8000/', 'a.stl')
      === 'http://x:8000/api/geometry/a.stl/download');
check('文件名被 URL 编码', geometryDownloadUrl('http://x', '零件 1.stl')
      === 'http://x/api/geometry/%E9%9B%B6%E4%BB%B6%201.stl/download',
      geometryDownloadUrl('http://x', '零件 1.stl'));

// 预览文件名规则必须与后端一致（backend/geometry.py 里是「原名 + .stl」）。
// 不一致只会表现为"重新打开项目时视口是空的"，极难排查。
check('STEP 预览名', renderFilenameFor('part.step') === 'part.step.stl');
check('STP 预览名', renderFilenameFor('part.STP') === 'part.STP.stl');
check('IGES 预览名', renderFilenameFor('part.iges') === 'part.iges.stl');
check('STL 保持原样', renderFilenameFor('part.stl') === 'part.stl');
check('中文名保持原样', renderFilenameFor('零件1.STEP') === '零件1.STEP.stl');

check('安全文件名', isSafeFilename('part.stl') && isSafeFilename('零件1.STEP.stl'));
check('拒绝路径穿越', !isSafeFilename('../a.stl')
      && !isSafeFilename('dir/a.stl') && !isSafeFilename('a\\b.stl'));
check('拒绝盘符与空名', !isSafeFilename('C:a.stl') && !isSafeFilename('')
      && !isSafeFilename('.'));
check('拒绝不在白名单的扩展名', !isSafeFilename('a.exe') && !isSafeFilename('a.sh'));

// 2) 错误文案：401 与 404 必须分开
check('401 提示重新登录', describeModelError(401, 'a.stl').includes('登录'));
check('404 说明文件不在', describeModelError(404, 'a.stl').includes('不存在'));
check('400 说明文件名不合法', describeModelError(400, 'a.stl').includes('不合法'));
check('网络层失败提到后端', describeModelError(null, 'a.stl').includes('后端'));

// 3) 带认证取回 —— 用注入的假 fetch / 假 URL，不需要浏览器
const makeFetch = (status, ok) => {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return { ok, status, blob: async () => new Blob(['fake']) };
  };
  impl.calls = calls;
  return impl;
};
const created = [];
const revoked = [];
const fakeUrl = (blob) => { const u = `blob:fake-${created.length}`; created.push(u); return u; };
const fakeRevoke = (u) => revoked.push(u);

// 成功路径：URL 正确、带上 Bearer、返回可 revoke 的 blob URL
let impl = makeFetch(200, true);
let result = await fetchModelBlobUrl('http://api', 'part.stl', {
  headers: { Authorization: 'Bearer tok-1' }, fetchImpl: impl, createObjectURL: fakeUrl,
  revokeObjectURL: fakeRevoke,
});
check('请求了正确的下载地址',
      impl.calls.length === 1
      && impl.calls[0].url === 'http://api/api/geometry/part.stl/download',
      impl.calls[0] && impl.calls[0].url);
check('请求带上了 Bearer 令牌',
      impl.calls[0].init.headers.Authorization === 'Bearer tok-1',
      JSON.stringify(impl.calls[0].init.headers));
check('成功时返回 blob URL', result.url === 'blob:fake-0', result.url);
result.revoke();
check('revoke 真的释放了', revoked.length === 1 && revoked[0] === 'blob:fake-0');

// 没有令牌时不发 "Bearer undefined"
impl = makeFetch(200, true);
await fetchModelBlobUrl('http://api', 'part.stl', {
  headers: {}, fetchImpl: impl, createObjectURL: fakeUrl,
  revokeObjectURL: fakeRevoke,
});
check('无令牌时不带 Authorization',
      impl.calls[0].init.headers.Authorization === undefined,
      JSON.stringify(impl.calls[0].init.headers));

// 失败路径：状态码要能透出来（上层据此决定"重新登录"还是"提示文件没了"）
const expectError = async (status, headers) => {
  const failing = makeFetch(status, false);
  try {
    await fetchModelBlobUrl('http://api', 'part.stl', {
      headers, fetchImpl: failing, createObjectURL: fakeUrl,
      revokeObjectURL: fakeRevoke,
    });
    return null;
  } catch (error) {
    return error;
  }
};
const unauthorized = await expectError(401, { Authorization: 'Bearer tok' });
check('401 抛 ModelLoadError 且带状态码',
      unauthorized instanceof ModelLoadError && unauthorized.status === 401,
      String(unauthorized));
const missing = await expectError(404, { Authorization: 'Bearer tok' });
check('404 抛 ModelLoadError 且带状态码',
      missing instanceof ModelLoadError && missing.status === 404);
check('404 文案可直接显示', missing.message.includes('不存在'), missing.message);

// 非法文件名在**发请求之前**就被挡住
impl = makeFetch(200, true);
const blocked = await (async () => {
  try {
    await fetchModelBlobUrl('http://api', '../secret.stl', {
      headers: { Authorization: 'Bearer t' }, fetchImpl: impl, createObjectURL: fakeUrl,
    });
    return null;
  } catch (error) { return error; }
})();
check('非法文件名被本地挡住（不发请求）',
      blocked instanceof ModelLoadError && blocked.status === 400
      && impl.calls.length === 0,
      `status=${blocked && blocked.status} calls=${impl.calls.length}`);

// 网络层失败（fetch 直接抛）与 HTTP 状态码区分开
const throwing = async () => { throw new Error('ECONNREFUSED'); };
const offline = await (async () => {
  try {
    await fetchModelBlobUrl('http://api', 'part.stl', {
      headers: { Authorization: 'Bearer t' }, fetchImpl: throwing, createObjectURL: fakeUrl,
    });
    return null;
  } catch (error) { return error; }
})();
check('网络失败 status 为 null 且文案提到后端',
      offline instanceof ModelLoadError && offline.status === null
      && offline.message.includes('后端'),
      offline && offline.message);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_model_source_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/modelSource.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _MODEL_SOURCE_SELFTEST)
    return ok, ("下载地址 / 预览名规则 / 失败路径 均符合断言" if ok else detail)


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
        info("=== 1/7 前端类型检查 ===")
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

            passed, detail = _check_frontend_auth_math(node_bin)
            check("认证工具（node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_project_setup_math(node_bin)
            check("项目配置自动保存（node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_model_source_math(node_bin)
            check("几何取用与鉴权（node 执行前端纯逻辑）", passed, detail)
        else:
            check("TypeScript 类型检查", False, "未找到 node")

    info("\n=== 2/7 后端可达性 ===")
    try:
        _http_json("GET", f"{API_BASE}/")
        check("GET /", True, "Simulation Backend is running")
        reachable = True
    except Exception as exc:
        check("GET /", False, f"{type(exc).__name__}: 后端未启动？先跑 python tools/tasks.py dev")
        reachable = False

    if reachable:
        info("\n=== 3/7 认证（后续所有检查都要带令牌）===")
        # 用**固定的测试账号**（登录优先，不存在才注册），避免每次 verify 都往
        # 开发库里塞新用户。用两个账号是为了能在真实 HTTP 栈上验证隔离——
        # 单测是直接构造 Authorization 头调 require_user 的，
        # **没有覆盖 FastAPI 的 Header 依赖注入**，那条链路只有这里能验。
        alice = bob = None
        api_headers: dict = {}
        try:
            unauth = _http_status("GET", f"{API_BASE}/api/projects")
            check(
                "未登录访问项目列表被拒（401）",
                unauth == 401,
                f"GET /api/projects（无令牌）-> HTTP {unauth}",
            )
            no_scheme = _http_status(
                "GET", f"{API_BASE}/api/projects",
                headers={"Authorization": "token-without-scheme"},
            )
            check(
                "非 Bearer 方案也被拒（401）",
                no_scheme == 401,
                f"Authorization: token-without-scheme -> HTTP {no_scheme}",
            )
            bad_token = _http_status(
                "GET", f"{API_BASE}/api/projects", headers=_bearer("not-a-real-token")
            )
            check(
                "伪造令牌被拒（401）",
                bad_token == 401,
                f"Bearer not-a-real-token -> HTTP {bad_token}",
            )

            # 求解/上传类端点现在也要求登录，必须逐个确认（它们以前是开放的）
            for path, method in (
                ("/api/materials", "GET"),
                ("/api/geometry/test_part.step/metadata", "GET"),
                ("/api/solve", "POST"),
                ("/api/jobs", "GET"),
            ):
                status = _http_status(method, f"{API_BASE}{path}", {})
                check(
                    f"未登录访问 {path} 被拒（401）",
                    status == 401,
                    f"{method} {path}（无令牌）-> HTTP {status}",
                )

            alice = _login_or_register("verify_alice", "verify-alice-pass")
            bob = _login_or_register("verify_bob", "verify-bob-pass")
            api_headers = _bearer(alice["token"])
            check(
                "登录返回令牌，且响应不含口令哈希",
                bool(alice.get("token")) and bool(alice.get("user", {}).get("id"))
                and "passwordHash" not in json.dumps(alice),
                f"alice id={alice.get('user', {}).get('id')}",
            )

            # 几何文件：从公开静态目录改成**需登录的下载端点**。
            # uploads/ 里既有 CAD 原件也有预览 STL，"知道文件名就能下载"
            # 与"项目按属主隔离"是互相矛盾的。
            #
            # 这里**先真的上传一个文件再下载**：既验证了"上传→下载"的往返，
            # 又不依赖开发机 uploads/ 里恰好有什么（预览 STL 只在上传时生成）。
            uploaded_name = _upload_probe_stl(api_headers)
            try:
                check(
                    "几何下载需要登录（401）",
                    _http_status(
                        "GET", f"{API_BASE}/api/geometry/{uploaded_name}/download"
                    ) == 401,
                    f"GET /api/geometry/{uploaded_name}/download（无令牌）",
                )
                downloaded = _http_download(
                    f"{API_BASE}/api/geometry/{uploaded_name}/download",
                    headers=api_headers,
                )
                check(
                    "带令牌能取到几何文件（含正确的 Content-Type）",
                    downloaded["status"] == 200 and downloaded["bytes"] > 50
                    and "stl" in str(downloaded.get("content_type", "")).lower(),
                    f"HTTP {downloaded['status']}，{downloaded['bytes']} 字节，"
                    f"Content-Type={downloaded.get('content_type')}",
                )
                check(
                    "公开的 /uploads/ 已关闭（不再能直接下载）",
                    _http_status("GET", f"{API_BASE}/uploads/{uploaded_name}") == 404,
                    "静态目录已移除：知道文件名也拿不到文件",
                )
            finally:
                _remove_uploaded_file(uploaded_name)

            check(
                "非法文件名被拒（400）",
                _http_status(
                    "GET", f"{API_BASE}/api/geometry/..%2Fetc%2Fpasswd.step/download",
                    headers=api_headers,
                ) in (400, 404),
                "路径穿越在下载端点同样被挡",
            )
            check(
                "不存在的文件是 404",
                _http_status(
                    "GET", f"{API_BASE}/api/geometry/nope.step/download",
                    headers=api_headers,
                ) == 404,
                "是「文件不在」而不是「参数错」",
            )

            me = _http_json("GET", f"{API_BASE}/api/auth/me",
                            headers=api_headers)
            check(
                "/auth/me 返回令牌对应的用户",
                me.get("id") == alice["user"]["id"]
                and me.get("username") == "verify_alice",
                f"username={me.get('username')}",
            )

            wrong_password = _http_status(
                "POST", f"{API_BASE}/api/auth/login",
                {"username": "verify_alice", "password": "definitely-wrong"},
            )
            unknown_user = _http_status(
                "POST", f"{API_BASE}/api/auth/login",
                {"username": "definitely_not_here", "password": "whatever-pass"},
            )
            check(
                "口令错误与用户不存在返回同样的 401（不给用户名枚举）",
                wrong_password == 401 and unknown_user == 401,
                f"错误口令={wrong_password}，不存在用户={unknown_user}",
            )
        except Exception as exc:
            import traceback

            traceback.print_exc()
            check("认证流程", False, f"{type(exc).__name__}: {exc}")

    if reachable:
        info("\n=== 4/7 API 冒烟测试 ===")
        try:
            materials = _http_json("GET", f"{API_BASE}/api/materials",
                                   headers=api_headers)
            check(
                "材料库",
                bool(materials) and materials[0].get("type") is not None,
                f"{len(materials)} 种，type={materials[0].get('type')}",
            )

            metadata = _http_json("GET", f"{API_BASE}/api/geometry/test_part.step/metadata", headers=api_headers)
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

        info("\n=== 5/7 求解器物理校准 ===")
        try:
            mesh = _http_json(
                "POST", f"{API_BASE}/api/generate-mesh?filename=test_part.step&mesh_size=1.2",
                headers=api_headers,
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
            result = _http_json("POST", f"{API_BASE}/api/solve", body, headers=api_headers)
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
                "POST", f"{API_BASE}/api/generate-mesh?filename=default_cube.step&mesh_size=1.5",
                headers=api_headers,
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
            cube_result = _http_json("POST", f"{API_BASE}/api/solve", cube_body, headers=api_headers)

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
            cube_mm = _http_json("POST", f"{API_BASE}/api/solve", dict(cube_body, length_unit="mm"), headers=api_headers)
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
                headers=api_headers,
            )
            job_payload: dict = {}
            for _ in range(120):
                time.sleep(0.5)
                job_payload = _http_json("GET", f"{API_BASE}/api/jobs/{submitted['job_id']}", headers=api_headers)
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
            thermal = _http_json("POST", f"{API_BASE}/api/thermal/solve", thermal_body, headers=api_headers)
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
                "POST", f"{API_BASE}/api/jobs/thermal", frontend_thermal,
                headers=api_headers,
            )
            thermal_job: dict = {}
            for _ in range(120):
                time.sleep(0.5)
                thermal_job = _http_json(
                    "GET", f"{API_BASE}/api/jobs/{submitted_thermal['job_id']}",
                    headers=api_headers,
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
                }, headers=api_headers)
                for _ in range(240):
                    time.sleep(0.5)
                    payload = _http_json(
                        "GET", f"{API_BASE}/api/jobs/{submitted_modal['job_id']}",
                        headers=api_headers,
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

    if reachable and alice and bob:
        info("\n=== 6/7 项目隔离（属主）===")
        created_id: Optional[str] = None
        try:
            marker = f"verify-{uuid.uuid4().hex[:8]}"
            alice_headers = _bearer(alice["token"])
            bob_headers = _bearer(bob["token"])

            created = _http_json(
                "POST", f"{API_BASE}/api/projects",
                {
                    "title": f"契约检查 {marker}",
                    "description": "由 tools/tasks.py verify 创建，跑完会删掉",
                    "simulationType": "FEA",
                    "isPrivate": True,
                },
                headers=alice_headers,
            )
            created_id = created.get("id")

            check(
                "创建项目返回服务端 ID、时间戳与属主",
                bool(created_id) and bool(created.get("createdAt"))
                and str(created.get("createdAt", "")).endswith("+00:00")
                and created.get("ownerId") == alice["user"]["id"],
                f"id={created_id} owner={created.get('ownerId')}",
            )

            listing = _http_json(
                "GET", f"{API_BASE}/api/projects", headers=alice_headers
            )
            ids = [item.get("id") for item in listing] if isinstance(listing, list) else []
            check(
                "新项目出现在自己的列表最前",
                bool(ids) and ids[0] == created_id,
                f"{len(ids)} 个项目，第一个={ids[0] if ids else None}",
            )

            fetched = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}",
                headers=alice_headers,
            )
            check(
                "按 ID 取回同一项目",
                fetched.get("id") == created_id
                and fetched.get("title") == f"契约检查 {marker}",
                f"title={fetched.get('title')}",
            )

            renamed = _http_json(
                "PATCH", f"{API_BASE}/api/projects/{created_id}",
                {"title": "改名后"}, headers=alice_headers,
            )
            check(
                "PATCH 只改给定字段",
                renamed.get("title") == "改名后"
                and renamed.get("description")
                == "由 tools/tasks.py verify 创建，跑完会删掉",
                f"title={renamed.get('title')}",
            )

            check(
                "服务端拒绝客户端自选 ID",
                _http_status(
                    "POST", f"{API_BASE}/api/projects",
                    {"id": "my-own-id", "title": "x"}, headers=alice_headers,
                ) == 422,
                "请求体里带 id 应为 422（否则客户端能覆盖别人的记录）",
            )
            check(
                "空标题被拒绝",
                _http_status(
                    "POST", f"{API_BASE}/api/projects",
                    {"title": "   "}, headers=alice_headers,
                ) == 422,
                "空标题应为 422",
            )

            # ---- 本轮的核心承诺：看不到、改不了别人的东西 ----
            bob_list = _http_json(
                "GET", f"{API_BASE}/api/projects", headers=bob_headers
            )
            bob_ids = (
                [item.get("id") for item in bob_list]
                if isinstance(bob_list, list) else []
            )
            check(
                "另一个用户看不到这个项目",
                created_id not in bob_ids,
                f"bob 有 {len(bob_ids)} 个项目",
            )
            check(
                "另一个用户读别人的项目是 404（不是 403）",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}",
                    headers=bob_headers,
                ) == 404,
                "404 而不是 403：不确认该 id 是否存在",
            )
            check(
                "另一个用户改不了别人的项目（404）",
                _http_status(
                    "PATCH", f"{API_BASE}/api/projects/{created_id}",
                    {"title": "被改了"}, headers=bob_headers,
                ) == 404,
                "改他人项目应为 404",
            )
            check(
                "另一个用户删不掉别人的项目（404）",
                _http_status(
                    "DELETE", f"{API_BASE}/api/projects/{created_id}",
                    headers=bob_headers,
                ) == 404,
                "删他人项目应为 404",
            )
            # 而且确实没有被改动
            still = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}",
                headers=alice_headers,
            )
            check(
                "被拒绝的操作没有产生任何副作用",
                still.get("title") == "改名后",
                f"title={still.get('title')}",
            )

            # ---- 项目配置（仿真设置的持久化）----
            # 在此之前几何/材料/边界条件/网格与求解设置全在浏览器内存里，
            # 重新打开项目是一个空白工作台。这组检查验证配置真的落库了。
            setup_document = {
                "version": 1,
                "geometryFilename": "test_part.step",
                "materialId": "structural_steel",
                "boundaryConditions": [
                    {"id": "bc_fix", "name": "固定端", "type": "fixed",
                     "applicationType": "face", "entityIndex": 1, "color": "#ff0000"},
                    {"id": "bc_load", "name": "拉力", "type": "force",
                     "applicationType": "face", "entityIndex": 2, "color": "#00ff00",
                     "force": {"x": 1000.0, "y": 0.0, "z": 0.0}},
                ],
                "meshSettings": {"id": "m1", "meshType": "tetrahedral",
                                 "meshSize": 1.2, "status": "meshed",
                                 "refinementRegions": [], "quality": 0.8},
                "solverSettings": {"id": "s1", "solverType": "structural",
                                   "solverName": "default", "lengthUnit": "mm",
                                   "parameters": {"timeStep": 0.1},
                                   "status": "configured"},
            }

            empty_setup = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                headers=alice_headers,
            )
            check(
                "新项目没有配置时返回 200 + null（不是 404）",
                empty_setup.get("setup") is None and empty_setup.get("savedAt") is None,
                f"setup={empty_setup.get('setup')}",
            )

            saved_setup = _http_json(
                "PUT", f"{API_BASE}/api/projects/{created_id}/setup",
                setup_document, headers=alice_headers,
            )
            check(
                "保存配置后原样读回",
                saved_setup.get("setup", {}).get("materialId") == "structural_steel"
                and len(saved_setup.get("setup", {}).get("boundaryConditions", [])) == 2
                and bool(saved_setup.get("savedAt")),
                f"materialId={saved_setup.get('setup', {}).get('materialId')} "
                f"savedAt={saved_setup.get('savedAt')}",
            )

            resolved = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                headers=alice_headers,
            )
            check(
                "重新取回配置内容一致（含中文与嵌套设置）",
                resolved.get("setup") == saved_setup.get("setup"),
                "两次读取应完全一致",
            )

            alice_list = _http_json(
                "GET", f"{API_BASE}/api/projects", headers=alice_headers
            )
            entry = next(
                (item for item in alice_list if item.get("id") == created_id), {}
            ) if isinstance(alice_list, list) else {}
            check(
                "列表里标出已配置，但**不带**完整配置",
                entry.get("hasSetup") is True and "setup" not in entry,
                f"hasSetup={entry.get('hasSetup')} keys={sorted(entry)[:4]}...",
            )

            check(
                "别人读不到我的配置（404）",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                    headers=bob_headers,
                ) == 404,
                "配置同样按属主隔离",
            )

            check(
                "非法几何文件名被拒（复用上传的校验规则）",
                _http_status(
                    "PUT", f"{API_BASE}/api/projects/{created_id}/setup",
                    dict(setup_document, geometryFilename="../../etc/passwd.step"),
                    headers=alice_headers,
                ) == 422,
                "配置里的文件名之后会被用于请求几何端点，必须同样校验",
            )

            oversized = dict(setup_document,
                             meshSettings={"blob": "x" * (300 * 1024)})
            check(
                "超大配置被拒（413）",
                _http_status(
                    "PUT", f"{API_BASE}/api/projects/{created_id}/setup",
                    oversized, headers=alice_headers,
                ) == 413,
                "配置是 UI 状态快照，正常只有几 KB",
            )

            cleared = _http_json(
                "DELETE", f"{API_BASE}/api/projects/{created_id}/setup",
                headers=alice_headers,
            )
            after_clear = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                headers=alice_headers,
            )
            check(
                "清空配置后项目还在、配置为空",
                cleared.get("cleared") is True and after_clear.get("setup") is None,
                f"setup={after_clear.get('setup')}",
            )

            # ---- 共享与协作权限 ----
            # 这是"可协作"三个字的核心：把项目交给别人一起做，且权限边界清楚。
            shared = _http_json(
                "POST", f"{API_BASE}/api/projects/{created_id}/shares",
                {"username": "verify_bob", "role": "viewer"},
                headers=alice_headers,
            )
            check(
                "共享给另一个用户（按用户名）",
                shared.get("username") == "verify_bob"
                and shared.get("role") == "viewer"
                and bool(shared.get("userId")),
                f"username={shared.get('username')} role={shared.get('role')}",
            )
            check(
                "共享响应不含任何口令字段",
                "password" not in json.dumps(shared).lower(),
                "响应里绝不能出现口令哈希",
            )

            bob_list = _http_json(
                "GET", f"{API_BASE}/api/projects", headers=bob_headers
            )
            bob_entry = next(
                (item for item in bob_list if item.get("id") == created_id), {}
            ) if isinstance(bob_list, list) else {}
            check(
                "被共享者在列表里看到它，且带角色",
                bob_entry.get("role") == "viewer",
                f"role={bob_entry.get('role')}",
            )
            check(
                "被共享者能读项目和配置",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}",
                    headers=bob_headers,
                ) == 200
                and _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                    headers=bob_headers,
                ) == 200,
                "只读也要能看内容",
            )

            viewer_put = _http_status(
                "PUT", f"{API_BASE}/api/projects/{created_id}/setup",
                setup_document, headers=bob_headers,
            )
            check(
                "只读者改不了配置（403，而不是 404）",
                viewer_put == 403,
                f"PUT setup（viewer）-> HTTP {viewer_put}；"
                "他知道项目存在，此时 403 比 404 更诚实",
            )
            check(
                "只读者改不了元信息、删不掉项目（404）",
                _http_status(
                    "PATCH", f"{API_BASE}/api/projects/{created_id}",
                    {"title": "我也来改"}, headers=bob_headers,
                ) == 404
                and _http_status(
                    "DELETE", f"{API_BASE}/api/projects/{created_id}",
                    headers=bob_headers,
                ) == 404,
                "改名/删除只属于属主",
            )
            check(
                "只有属主能查看共享名单",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/shares",
                    headers=alice_headers,
                ) == 200
                and _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/shares",
                    headers=bob_headers,
                ) == 403,
                "被共享者不需要知道还有谁",
            )

            # 升级为 editor 之后就能改配置了——这才是"一起做"
            upgraded = _http_json(
                "POST", f"{API_BASE}/api/projects/{created_id}/shares",
                {"username": "verify_bob", "role": "editor"},
                headers=alice_headers,
            )
            shares_now = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/shares",
                headers=alice_headers,
            )
            check(
                "重复共享是改角色，不会堆出重复条目",
                upgraded.get("role") == "editor"
                and len(shares_now) == 1,
                f"名单长度={len(shares_now)}",
            )
            bob_saved = _http_status(
                "PUT", f"{API_BASE}/api/projects/{created_id}/setup",
                setup_document, headers=bob_headers,
            )
            check(
                "可编辑协作者能保存配置（属主看得到）",
                bob_saved == 200
                and (
                    _http_json(
                        "GET", f"{API_BASE}/api/projects/{created_id}/setup",
                        headers=alice_headers,
                    ).get("setup", {}).get("materialId") == "structural_steel"
                ),
                f"PUT setup（editor）-> HTTP {bob_saved}",
            )

            check(
                "共享给自己被拒（400）",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/shares",
                    {"username": "verify_alice", "role": "viewer"},
                    headers=alice_headers,
                ) == 400,
                "共享给自己没有意义",
            )
            check(
                "共享给不存在的用户是 404",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/shares",
                    {"username": "definitely_not_here", "role": "viewer"},
                    headers=alice_headers,
                ) == 404,
                "是「没有这个人」而不是「参数错」",
            )
            check(
                "非法角色是 422",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/shares",
                    {"username": "verify_bob", "role": "admin"},
                    headers=alice_headers,
                ) == 422,
                "角色只有 viewer / editor",
            )

            # 被共享者可以自己退出（否则他没法退出一个共享）
            left = _http_json(
                "DELETE",
                f"{API_BASE}/api/projects/{created_id}/shares/{shared['userId']}",
                headers=bob_headers,
            )
            check(
                "被共享者可以自己退出，退出后立即失去访问",
                left.get("removed") is True
                and _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}",
                    headers=bob_headers,
                ) == 404,
                "退出后再读应为 404",
            )

            _http_json(
                "DELETE", f"{API_BASE}/api/projects/{created_id}",
                headers=alice_headers,
            )
            deleted_status = _http_status(
                "GET", f"{API_BASE}/api/projects/{created_id}",
                headers=alice_headers,
            )
            check(
                "删除后确实不存在（404 而不是静默成功）",
                deleted_status == 404,
                f"GET 已删除项目 -> HTTP {deleted_status}",
            )
            created_id = None

            # ---- 遗留项目（owner_id IS NULL）：可见、不可改、可显式认领 ----
            # 这类数据只能靠直接写库造出来（HTTP 接口创建的项目一定有属主），
            # 因此这里显式插一行再删掉，模拟"接上登录之前的数据库"。
            legacy_id = f"legacy{uuid.uuid4().hex[:6]}"
            try:
                _insert_legacy_project(legacy_id, "遗留项目（verify 临时造）")
                visible = _http_json(
                    "GET", f"{API_BASE}/api/projects", headers=alice_headers
                )
                legacy_row = next(
                    (item for item in visible if item.get("id") == legacy_id), None
                ) if isinstance(visible, list) else None
                check(
                    "无主项目对已登录用户可见，且仍标为未归属",
                    legacy_row is not None and legacy_row.get("ownerId") is None,
                    f"ownerId={legacy_row.get('ownerId') if legacy_row else '未找到'}",
                )
                check(
                    "无主项目在认领前不可删（404）",
                    _http_status(
                        "DELETE", f"{API_BASE}/api/projects/{legacy_id}",
                        headers=alice_headers,
                    ) == 404,
                    "应先认领再操作，避免任意用户改动遗留数据",
                )
                claimed = _http_json(
                    "POST", f"{API_BASE}/api/projects/{legacy_id}/claim",
                    None, headers=alice_headers,
                )
                check(
                    "认领后归属变为当前用户",
                    claimed.get("ownerId") == alice["user"]["id"],
                    f"ownerId={claimed.get('ownerId')}",
                )
                check(
                    "别人不能认领已经属于他人的项目（404）",
                    _http_status(
                        "POST", f"{API_BASE}/api/projects/{legacy_id}/claim",
                        None, headers=bob_headers,
                    ) == 404,
                    "认领不能变成「任意项目过户」",
                )
                renamed_after_claim = _http_json(
                    "PATCH", f"{API_BASE}/api/projects/{legacy_id}",
                    {"title": "认领后可改名"}, headers=alice_headers,
                )
                check(
                    "认领后可以正常改名",
                    renamed_after_claim.get("title") == "认领后可改名",
                    f"title={renamed_after_claim.get('title')}",
                )
            except Exception as exc:
                import traceback

                traceback.print_exc()
                check("遗留项目与认领流程", False, f"{type(exc).__name__}: {exc}")
            finally:
                _delete_legacy_project(legacy_id)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            check("项目管理 CRUD 与隔离", False, f"{type(exc).__name__}: {exc}")
        finally:
            # 检查用的项目不要留在用户的数据库里
            # （测试账号本身会留下，`verify_alice` / `verify_bob` 是可预期的）
            if created_id and alice:
                try:
                    _http_json(
                        "DELETE", f"{API_BASE}/api/projects/{created_id}",
                        headers=_bearer(alice["token"]),
                    )
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
