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


#: 网格质量的显示逻辑（纯函数）。
#:
#: 重点钉的是"缺失 vs 0"这个区分：质量真的是 0（退化单元）和"后端没给这个字段"
#: 在界面上必须长得不一样。本项目已经吃过一次亏——`formatFrequency(null)`
#: 曾经返回 `'0 Hz'`，于是"数据缺失"被显示成"刚体模态"。
_MESH_QUALITY_SELFTEST = r"""
import { barHeight, describeQualitySummary, describeQualityWarnings,
         formatQuality, histogramIsComplete, maxBinCount,
         toMeshQuality } from './meshQuality.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 一份与后端响应同形的数据（字段名照抄 /api/mesh-quality）
const histogram = [];
for (let i = 0; i < 10; i += 1) {
  histogram.push({ lo: i / 10, hi: (i + 1) / 10, count: i === 5 ? 100 : 1 });
}
const payload = {
  status: 'ok', filename: 'default_cube.step', elements: 109, nodes: 40,
  total_volume: 1000.0,
  quality: { min: 0.2671, max: 1.0, mean: 0.7559, median: 0.8, p05: 0.31 },
  edge_ratio_max: 1.9, poor_count: 0, non_positive_volume_count: 0,
  non_finite_count: 0, histogram,
  worst_elements: [{ index: 12, quality: 0.2671, volume: 0.5, edgeRatio: 1.9 }],
  poor_threshold: 0.1,
  verdict: '单元形状质量良好。注意：形状好不等于网格够细，密度是否足够要看收敛性。',
};

const mapped = toMeshQuality(payload);
check('能解析后端响应', mapped !== null);
check('字段名映射正确（snake_case -> camelCase）',
      mapped.elements === 109 && mapped.nodes === 40
      && mapped.edgeRatioMax === 1.9 && mapped.poorThreshold === 0.1,
      JSON.stringify({ e: mapped.elements, n: mapped.nodes, r: mapped.edgeRatioMax }));
check('统计量完整', mapped.stats.min === 0.2671 && mapped.stats.p05 === 0.31);
check('结论原样保留', mapped.verdict.includes('收敛'));
check('最差单元被解析', mapped.worstElements.length === 1
      && mapped.worstElements[0].index === 12);

// --- 缺失必须是"缺失"，不能变成 0 -------------------------------------------
check('缺失输入返回 null（而不是一堆 0）',
      toMeshQuality(null) === null && toMeshQuality(undefined) === null
      && toMeshQuality('nope') === null && toMeshQuality(42) === null);
check('缺 quality 统计量返回 null',
      toMeshQuality({ ...payload, quality: undefined }) === null);
check('缺 histogram 返回 null',
      toMeshQuality({ ...payload, histogram: undefined }) === null);
check('直方图为空返回 null', toMeshQuality({ ...payload, histogram: [] }) === null);
check('单元数为 0 返回 null', toMeshQuality({ ...payload, elements: 0 }) === null);
check('min/max 非数字返回 null',
      toMeshQuality({ ...payload, quality: { ...payload.quality, min: null } }) === null);

// 关键：真实的值 0 必须仍然显示成 0，只有"非数字"才显示 '—'
check('质量 0 显示为 0.0000', formatQuality(0) === '0.0000', formatQuality(0));
check('缺失显示为破折号', formatQuality(null) === '—' && formatQuality(undefined) === '—'
      && formatQuality('') === '—' && formatQuality('0.5') === '—' && formatQuality(NaN) === '—',
      `${formatQuality(null)} ${formatQuality('0.5')}`);
check('数值保留 4 位', formatQuality(0.26705) === '0.2671', formatQuality(0.26705));

// --- 条形高度：空直方图不能产生 NaN ------------------------------------------
check('最大箱计数', maxBinCount(histogram) === 100, String(maxBinCount(histogram)));
check('空直方图的最大计数为 0',
      maxBinCount([]) === 0 && maxBinCount(null) === 0);
check('条形高度归一化', barHeight(50, 100) === 0.5, String(barHeight(50, 100)));
check('计数为 0 时高度为 0', barHeight(0, 100) === 0);
check('除数为 0 时不产生 NaN', barHeight(3, 0) === 0 && Number.isFinite(barHeight(3, 0)));
check('垃圾输入不产生 NaN', barHeight(null, null) === 0 && barHeight('x', 5) === 0);

// --- 摘要与提醒 ---------------------------------------------------------------
const summary = describeQualitySummary(mapped);
check('摘要先给最小值（判断网格能不能用看最差的那个单元）',
      summary.startsWith('最低 0.2671'), summary);
check('摘要带上单元数', summary.includes('109 单元'), summary);
check('没有数据时的摘要不说"很好"',
      describeQualitySummary(null) === '尚未检查', describeQualitySummary(null));

check('健康网格的提醒', describeQualityWarnings(mapped).join('|').includes('未发现畸形单元'),
      describeQualityWarnings(mapped).join('|'));
const bad = toMeshQuality({ ...payload, poor_count: 3, non_positive_volume_count: 2 });
const badWarnings = describeQualityWarnings(bad).join('|');
check('畸形单元数被说出来', badWarnings.includes('2 个体积非正') && badWarnings.includes('3 个单元质量低于'),
      badWarnings);
check('没有数据时不给提醒', describeQualityWarnings(null).length === 0);

// --- 直方图自洽性 -------------------------------------------------------------
check('一致的直方图判定为自洽', histogramIsComplete(mapped));
check('计数对不上时判定为不自洽',
      !histogramIsComplete(toMeshQuality({ ...payload, elements: 110 })));
check('没有数据时不自洽', !histogramIsComplete(null));

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


#: 把**后端真实返回**的网格质量喂给前端解析逻辑。
#:
#: 与模态那条同理：后端字段一旦改名（`edge_ratio_max` -> `edgeRatioMax`），
#: 界面只会静默地少显示一项，不报错、不影响任何数值。只有拿真实响应跑一遍
#: 前端的解析才能发现。而且这里额外断言"直方图计数之和 = 单元数"——
#: 那条只有在两端对"什么算一个单元"的理解一致时才成立。
_MESH_QUALITY_DISPLAY_CHAIN_SELFTEST = r"""
import { readFileSync } from 'node:fs';
import { describeQualitySummary, histogramIsComplete,
         toMeshQuality } from './meshQuality.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const raw = JSON.parse(readFileSync(process.env.SIMCLOUD_MESH_QUALITY_PAYLOAD, 'utf8'));
const quality = toMeshQuality(raw);

check('真实响应能被前端解析（否则界面什么都不显示）', quality !== null);
if (quality) {
  check('单元数一致', quality.elements === raw.elements,
        `${quality.elements} vs ${raw.elements}`);
  check('节点数一致', quality.nodes === raw.nodes);
  check('最小值一致', quality.stats.min === raw.quality.min);
  check('最大值一致', quality.stats.max === raw.quality.max);
  check('棱长比上限映射成功', quality.edgeRatioMax === raw.edge_ratio_max,
        `${quality.edgeRatioMax} vs ${raw.edge_ratio_max}`);
  check('直方图自洽（各箱计数之和 = 单元数）', histogramIsComplete(quality));
  check('直方图箱数与后端一致', quality.histogram.length === raw.histogram.length);
  check('结论非空且点出收敛性', quality.verdict.length > 0 && quality.verdict.includes('收敛'),
        quality.verdict);
  check('摘要能直接显示', describeQualitySummary(quality).includes(`${raw.elements} 单元`),
        describeQualitySummary(quality));
  if (raw.worst_elements.length > 0) {
    check('最差单元被解析', quality.worstElements.length === raw.worst_elements.length);
    check('最差单元的质量与后端一致',
          quality.worstElements[0].quality === raw.worst_elements[0].quality);
  }
}

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_mesh_quality_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/meshQuality.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _MESH_QUALITY_SELFTEST)
    return ok, ("缺失≠0 / 完整映射 / 直方图自洽性 均符合断言" if ok else detail)


#: 收敛检查的显示逻辑（纯函数）。
#:
#: 重点钉三件事：
#:   1. **四态不能压成两种**——`marginal`（在趋稳但没到阈值）既不是通过也
#:      不是失败，而它恰恰是最常见的状态；
#:   2. **缺失 ≠ 0**——收敛检查里的 0 有意义（"最后一级变化 0%"），
#:      所以 `null` / 非数字必须显示成 `—`；
#:   3. **趋势图的横轴用实测单元数折算的 h**，不用"第几级"——各级实际加密
#:      幅度并不相等，按级数画会把不等距的点画成等距，看着像漂亮的收敛曲线。
_CONVERGENCE_STUDY_SELFTEST = r"""
import { describeStudyStatus, describeStudySummary, formatNumber, formatOrder,
         formatPercent, formatQuantity, quantityLabel, studyRows,
         toConvergenceStudy, trendPoints } from './convergenceStudy.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 一份与后端响应同形的数据（字段名照抄 /api/convergence/study）
const raw = {
  status: 'marginal',
  analysis_type: 'structural',
  primary: 'max_stress',
  quantities: ['max_stress', 'max_displacement'],
  labels: { max_stress: '最大 von Mises 应力', max_displacement: '最大位移' },
  levels: [
    { label: 'mesh_size=3', mesh_size: 3, elements: 426, nodes: 145,
      quantities: { max_stress: 29.02e6, max_displacement: 1.084e-9 } },
    { label: 'mesh_size=1.5', mesh_size: 1.5, elements: 1366, nodes: 467,
      quantities: { max_stress: 39.80e6, max_displacement: 1.264e-9 } },
    { label: 'mesh_size=0.75', mesh_size: 0.75, elements: 9462, nodes: 2430,
      quantities: { max_stress: 55.65e6, max_displacement: 1.617e-9 } },
    { label: 'mesh_size=0.375', mesh_size: 0.375, elements: 66837, nodes: 12000,
      quantities: { max_stress: 68.87e6, max_displacement: 1.826e-9 } },
  ],
  assessments: {
    max_stress: {
      label: '最大 von Mises 应力', values: [29.02e6, 39.80e6, 55.65e6, 68.87e6],
      differences: [10.78e6, 15.85e6, 13.22e6], levels: 4, monotone: true,
      observed_order: 0.295, order_estimator: 'generalized',
      extrapolated_limit: 131.2e6, last_relative_change: 0.1919687,
      converged: true, verdict: '…', mode: null,
    },
    max_displacement: {
      label: '最大位移', values: [1.084e-9, 1.264e-9, 1.617e-9, 1.826e-9],
      differences: [1.80e-10, 3.53e-10, 2.09e-10], levels: 4, monotone: true,
      observed_order: 0.822, order_estimator: 'generalized',
      extrapolated_limit: 2.12e-9, last_relative_change: 0.11456,
      converged: true, verdict: '…', mode: null,
    },
  },
  tolerance: 0.05,
  verdict: '所有考察量都在单调趋稳，但最大 von Mises 应力最后一级仍变化 19.20%…',
  notes: ['本检查是**自收敛**：…', '…应力奇异…', '…临时副本…'],
  warnings: [],
};

const study = toConvergenceStudy(raw);
check('能解析后端响应', study !== null);
check('字段名映射（snake_case -> camelCase）',
      study.primary === 'max_stress' && study.tolerance === 0.05
      && study.levels[3].meshSize === 0.375 && study.levels[3].elements === 66837,
      JSON.stringify({ primary: study.primary, tol: study.tolerance }));
check('考察量顺序保留', study.quantities.join(',') === 'max_stress,max_displacement');
check('判定被解析并映射',
      study.assessments.max_stress.orderEstimator === 'generalized'
      && study.assessments.max_stress.observedOrder === 0.295
      && study.assessments.max_stress.lastRelativeChange === 0.1919687);
check('说明与警告数组保留',
      study.notes.length === 3 && study.warnings.length === 0);

// --- 四态不能压成两种 ---------------------------------------------------------
const states = ['converged', 'marginal', 'not-converged', 'insufficient']
  .map(status => describeStudyStatus(status));
check('四态各有文案', new Set(states.map(item => item.label)).size === 4,
      JSON.stringify(states.map(item => item.label)));
check('marginal 既不是绿也不是红',
      describeStudyStatus('marginal').tone === 'warn',
      describeStudyStatus('marginal').tone);
check('not-converged 是坏', describeStudyStatus('not-converged').tone === 'bad');
check('级数不足单独一态（不当作失败）',
      describeStudyStatus('insufficient').tone === 'info'
      && describeStudyStatus('insufficient').label.includes('无法判断'));
check('未知状态有兜底', describeStudyStatus('nonsense').tone === 'info');

// --- 缺失 ≠ 0 -----------------------------------------------------------------
check('数值 0 显示成 0', formatNumber(0) === '0', formatNumber(0));
check('缺失显示破折号',
      formatNumber(null) === '—' && formatNumber(undefined) === '—'
      && formatNumber('1') === '—' && formatNumber(NaN) === '—');
check('百分比 0 显示成 0.00%', formatPercent(0) === '0.00%', formatPercent(0));
check('百分比缺失显示破折号', formatPercent(null) === '—' && formatPercent('x') === '—');
check('收敛阶 0 与「无法判断」分开',
      formatOrder(0) === '0.00' && formatOrder(null) === '无法判断'
      && formatOrder(undefined) === '无法判断',
      `${formatOrder(0)} / ${formatOrder(null)}`);

// --- 单位换算（后端一律 SI） ---------------------------------------------------
check('应力 Pa -> MPa', formatQuantity(29.02e6, 'max_stress').endsWith('MPa'),
      formatQuantity(29.02e6, 'max_stress'));
check('应力换算数值正确',
      formatQuantity(1e6, 'max_stress') === '1.000 MPa',
      formatQuantity(1e6, 'max_stress'));
check('位移 m -> mm', formatQuantity(1e-3, 'max_displacement') === '1.000 mm',
      formatQuantity(1e-3, 'max_displacement'));
check('温度用 K（与结论里的相对变化口径一致）',
      formatQuantity(373.15, 'max_temperature') === '373.1 K',
      formatQuantity(373.15, 'max_temperature'));
check('未知量走通用格式', formatQuantity(3.5, 'something_else') === '3.500');
check('缺失的量显示破折号', formatQuantity(null, 'max_stress') === '—');

check('考察量显示名用后端的', quantityLabel(study, 'max_stress') === '最大 von Mises 应力');
check('没有名字时回落到键名', quantityLabel(study, 'unknown_q') === 'unknown_q');
// 没有 study（例如求解记录那条路径）时用本地兜底表，而不是把英文键名丢给用户
check('没有数据时用本地兜底标签',
      quantityLabel(null, 'max_stress') === '最大 von Mises 应力',
      quantityLabel(null, 'max_stress'));
check('兜底表里没有的量仍然原样返回',
      quantityLabel(null, 'something_new') === 'something_new');

// --- 逐级数表 -----------------------------------------------------------------
const rows = studyRows(study);
check('表行数与级别数一致', rows.length === 4, String(rows.length));
check('表里带格式化后的数值（SI -> 显示单位）',
      rows[0].formatted.max_stress === '29.02 MPa'
      && rows[0].formatted.max_displacement === '1.08e-6 mm',
      JSON.stringify(rows[0].formatted));
check('没有数据时表为空', studyRows(null).length === 0);

// --- 趋势图 -------------------------------------------------------------------
const points = trendPoints(study, 'max_stress');
check('趋势图有 4 个点', points.length === 4, String(points.length));
check('横轴最粗为 0、最细为 1',
      Math.abs(points[0].x) < 1e-12 && Math.abs(points[points.length - 1].x - 1) < 1e-12,
      points.map(point => point.x.toFixed(3)).join(','));
check('横轴按单元数（不是按第几级）排列',
      points.every((point, index) => index === 0 || point.x > points[index - 1].x),
      points.map(point => point.x.toFixed(3)).join(','));
// 各级实际加密幅度不等 ⇒ 横轴**不是**等距的。这条把"按级数画"钉死：
// 若误用级数下标归一化，第二个点会是 0.333，而按单元数应是 0.230。
check('横轴不等距（证明用的是单元数而不是级数下标）',
      Math.abs(points[1].x - 1 / 3) > 0.05,
      `x[1]=${points[1].x.toFixed(4)}（等距应为 0.3333）`);
check('纵轴归一化到 [0.1, 0.9] 之内',
      points.every(point => point.y >= 0.1 - 1e-12 && point.y <= 0.9 + 1e-12),
      points.map(point => point.y.toFixed(3)).join(','));
check('数值最大的点画在最上方',
      points[3].y > points[0].y && Math.abs(points[3].y - 0.9) < 1e-12,
      String(points[3].y));
check('全部相同时不画成斜线（返回平的中线）',
      trendPoints({ ...study, levels: study.levels.map(level => ({
        ...level, quantities: { max_stress: 5, max_displacement: 1 },
      })) }, 'max_stress').every(point => Math.abs(point.y - 0.5) < 1e-12));
check('只有一个点时不给图', trendPoints({ ...study, levels: [study.levels[0]] },
      'max_stress').length === 0);
check('缺数据时不给图', trendPoints(null, 'max_stress').length === 0);

// --- 摘要 ---------------------------------------------------------------------
const summary = describeStudySummary(study);
check('摘要给出级数', summary.includes('4 级网格'), summary);
check('摘要给出最后一级变化', summary.includes('19.20%'), summary);
check('没有数据时的摘要不说「已收敛」',
      describeStudySummary(null) === '尚未检查', describeStudySummary(null));
check('零级时说明没取到级别',
      describeStudySummary({ ...study, levels: [] }).includes('没有取得'),
      describeStudySummary({ ...study, levels: [] }));

// --- 不可用输入一律返回 null（宁可什么都不显示，也不要显示编造的结论）---------
check('null / 非对象返回 null',
      toConvergenceStudy(null) === null && toConvergenceStudy('x') === null
      && toConvergenceStudy(7) === null);
check('没有 status 返回 null', toConvergenceStudy({ ...raw, status: undefined }) === null);
check('failed 返回 null', toConvergenceStudy({ ...raw, status: 'failed' }) === null);
check('缺 levels 返回 null', toConvergenceStudy({ ...raw, levels: undefined }) === null);
check('缺 assessments 返回 null',
      toConvergenceStudy({ ...raw, assessments: undefined }) === null);
check('非 insufficient 却没有级别时返回 null',
      toConvergenceStudy({ ...raw, levels: [] }) === null);
check('insufficient 允许零级（第一级就超上限时确实可能是 0 级）',
      toConvergenceStudy({ ...raw, status: 'insufficient', levels: [], assessments: {} })
      !== null);
check('级别里缺 elements 视为坏数据',
      toConvergenceStudy({ ...raw, levels: [{ label: 'x', mesh_size: 1 }] }) === null);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_convergence_study_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/convergenceStudy.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _CONVERGENCE_STUDY_SELFTEST)
    return ok, ("四态区分 / 缺失≠0 / 单位换算 / 趋势图横轴 均符合断言" if ok else detail)


#: 结果导出（CSV / VTK）的纯逻辑。
#:
#: 这一层最要紧的一条契约是**精度不能丢**：导出的文本必须能精确还原原始双精度
#: 数。写成 `toFixed(6)` 之类的做法会静默丢精度——文件看着完全正常，数值已经
#: 不对了。所以这里直接断言 `Number(写出来的文本) === 原始值`。
#:
#: 另外钉两件事：长度不一致要**拒绝导出**（否则位移会被贴到别的节点上，
#: 而文件依然合法、依然能打开）；行尾约定（CSV 用 CRLF、VTK 用 LF）不能被
#: "顺手统一"。
_RESULT_EXPORT_SELFTEST = r"""
import { buildLegacyVtk, buildResultCsv, csvCell, describeExportProblem,
         exportFilename, filenameStamp, validateExportFields,
         validateExportMesh } from './resultExport.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 两个四面体的小网格（元素是 **0 基**索引，与 VTK 一致）
const mesh = {
  nodes: [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]],
  elements: [[0, 1, 2, 3], [1, 2, 3, 4]],
};
const displacement = [
  [0, 0, 0], [1e-9, -2.5e-10, 3.75e-11], [0.1, 0.2, 0.3],
  [1 / 3, 2 / 3, 1], [-0, 1.5, -2.25],
];
const vonMises = [0, 1.2345678901234567e7, 0.1, 1e-300, 123456.789];
const fields = {
  vectors: [{ name: 'displacement_m', values: displacement }],
  scalars: [{ name: 'von_mises_Pa', values: vonMises }],
};

// --- CSV ----------------------------------------------------------------------
const csv = buildResultCsv(mesh, fields);
check('CSV 能生成', typeof csv === 'string' && csv.length > 0);
const csvLines = csv.split('\r\n');
check('CSV 表头带单位', csvLines[0] === 'node,x,y,z,displacement_m_x,displacement_m_y,'
      + 'displacement_m_z,von_mises_Pa', csvLines[0]);
check('CSV 行数 = 节点数 + 表头 + 末尾空行', csvLines.length === mesh.nodes.length + 2,
      String(csvLines.length));
check('CSV 第 0 行数值正确',
      csvLines[1] === '0,0,0,0,0,0,0,0', csvLines[1]);
check('CSV 第 1 行数值正确',
      csvLines[2] === '1,1,0,0,1e-9,-2.5e-10,3.75e-11,12345678.901234567', csvLines[2]);
check('CSV 用 CRLF（RFC 4180，Excel 友好）',
      csv.includes('\r\n') && !/(^|[^\r])\n/.test(csv), JSON.stringify(csv.slice(0, 40)));
check('CSV 以换行收尾', csv.endsWith('\r\n'));

// 精度：写出来的文本必须能**精确还原**原始双精度数
const tricky = [0.1, 1 / 3, 1e-300, 1.2345678901234567e7, -0, 1e21, 5e-324];
const precisionOk = tricky.every(value => {
  const text = value === 0 ? '0' : String(value);
  return Number(text) === value;
});
check('JS 的 String() 是精确往返的（这是选它的唯一理由）', precisionOk);
check('CSV 里的数能精确还原',
      Number(csvLines[2].split(',')[4]) === displacement[1][0]
      && Number(csvLines[2].split(',')[7]) === vonMises[1],
      `${Number(csvLines[2].split(',')[7])} vs ${vonMises[1]}`);
check('toFixed 会丢精度（反证：所以不能用它）',
      Number((1.2345678901234567e7).toFixed(6)) !== 1.2345678901234567e7);

// 每行列数一致（否则不同解析器会各行其是）
const columnCounts = new Set(csv.trimEnd().split('\r\n').map(line => line.split(',').length));
check('CSV 每行列数一致', columnCounts.size === 1, JSON.stringify([...columnCounts]));
check('列数 = 4 + 3 + 1', [...columnCounts][0] === 8, String([...columnCounts][0]));

// 没有场时也能导出（只有几何）
const plainCsv = buildResultCsv(mesh, {});
check('没有结果场时 CSV 只有坐标', plainCsv.split('\r\n')[0] === 'node,x,y,z');

// 转义：字段名里有逗号/引号时必须包起来
check('csvCell 逗号转义', csvCell('a,b') === '"a,b"', csvCell('a,b'));
check('csvCell 引号转义', csvCell('a"b') === '"a""b"', csvCell('a"b'));
check('csvCell 换行转义', csvCell('a\nb') === '"a\nb"');
check('csvCell 普通文本不动', csvCell('von_mises_Pa') === 'von_mises_Pa');
check('字段名含逗号时表头被引起来',
      buildResultCsv(mesh, { scalars: [{ name: 'a,b', values: vonMises }] })
        .split('\r\n')[0].endsWith('"a,b"'));

// --- VTK ----------------------------------------------------------------------
const vtk = buildLegacyVtk(mesh, fields);
check('VTK 能生成', typeof vtk === 'string' && vtk.length > 0);
const vtkLines = vtk.split('\n');
check('VTK 头四行固定',
      vtkLines[0] === '# vtk DataFile Version 3.0'
      && vtkLines[1] === 'SimCloud AI result export'
      && vtkLines[2] === 'ASCII'
      && vtkLines[3] === 'DATASET UNSTRUCTURED_GRID',
      JSON.stringify(vtkLines.slice(0, 4)));
check('VTK 用 LF（便于 diff；ParaView 两种都认）',
      !vtk.includes('\r'), JSON.stringify(vtk.slice(0, 40)));
check('POINTS 行给出节点数', vtkLines[4] === `POINTS ${mesh.nodes.length} double`,
      vtkLines[4]);
check('坐标行数 = 节点数', vtkLines[5] === '0 0 0' && vtkLines[9] === '1 1 1');
check('CELLS 行给出单元数与连接表长度',
      vtkLines[10] === `CELLS ${mesh.elements.length} ${mesh.elements.length * 5}`,
      vtkLines[10]);
check('每行单元是 4 + 四个索引',
      vtkLines[11] === '4 0 1 2 3' && vtkLines[12] === '4 1 2 3 4', vtkLines[11]);
check('CELL_TYPES 全部是 10（VTK_TETRA）',
      vtkLines[13] === `CELL_TYPES ${mesh.elements.length}`
      && vtkLines[14] === '10' && vtkLines[15] === '10');
check('POINT_DATA 行给出节点数', vtkLines[16] === `POINT_DATA ${mesh.nodes.length}`,
      vtkLines[16]);
check('VECTORS 声明带场名与类型',
      vtkLines[17] === 'VECTORS displacement_m double', vtkLines[17]);
check('矢量数据行数与节点数一致且数值精确',
      vtkLines[19] === '1e-9 -2.5e-10 3.75e-11'
      && Number(vtkLines[19].split(' ')[0]) === displacement[1][0],
      vtkLines[19]);
check('SCALARS 声明带 LOOKUP_TABLE（legacy 格式硬性要求）',
      vtkLines[23] === 'SCALARS von_mises_Pa double 1'
      && vtkLines[24] === 'LOOKUP_TABLE default',
      `${vtkLines[23]} / ${vtkLines[24]}`);
check('标量数值精确',
      Number(vtkLines[26]) === vonMises[1], vtkLines[26]);

// 段计数自洽：这是"文件能被读回来"的最基本条件
const pointsCount = Number(vtkLines[4].split(' ')[1]);
const coordinateLines = vtkLines.slice(5, 5 + pointsCount).length;
const cellsCount = Number(vtkLines[10].split(' ')[1]);
const cellLines = vtkLines.slice(11, 11 + cellsCount).length;
const pointDataCount = Number(vtkLines[16].split(' ')[1]);
const vectorLines = vtkLines.slice(18, 18 + pointDataCount).length;
const scalarLines = vtkLines.slice(25, 25 + pointDataCount).length;
check('各段行数与声明一致',
      coordinateLines === pointsCount && cellLines === cellsCount
      && vectorLines === pointDataCount && scalarLines === pointDataCount,
      `${coordinateLines}/${pointsCount} ${cellLines}/${cellsCount} `
      + `${vectorLines}/${pointDataCount} ${scalarLines}/${pointDataCount}`);
check('没有场时不写 POINT_DATA', !buildLegacyVtk(mesh, {}).includes('POINT_DATA'));

// --- 数据不自洽必须**拒绝导出**，而不是生成一个看着正常的文件 ----------------
check('长度不一致的标量场被拒',
      buildResultCsv(mesh, { scalars: [{ name: 's', values: [1, 2] }] }) === null);
check('长度不一致的矢量场被拒',
      buildLegacyVtk(mesh, { vectors: [{ name: 'v', values: [[1, 2, 3]] }] }) === null);
check('长度不一致会给出可读原因',
      (describeExportProblem(mesh, { scalars: [{ name: 's', values: [1] }] }) || '')
        .includes('不一致'),
      describeExportProblem(mesh, { scalars: [{ name: 's', values: [1] }] }));
check('NaN 被拒（CSV/VTK 里都没有合法写法）',
      buildResultCsv(mesh, { scalars: [{ name: 's', values: [0, 0, 0, NaN, 0] }] }) === null
      && buildLegacyVtk(mesh, {
        vectors: [{ name: 'v', values: [[0, 0, 0], [0, 0, 0], [0, 0, 0],
                                        [0, 0, Infinity], [0, 0, 0]] }],
      }) === null);
check('单元引用越界节点被拒',
      buildResultCsv({ nodes: mesh.nodes, elements: [[0, 1, 2, 99]] }, {}) === null);
check('单元索引非整数被拒',
      buildResultCsv({ nodes: mesh.nodes, elements: [[0, 1, 2, 3.5]] }, {}) === null);
check('空网格被拒',
      buildResultCsv({ nodes: [], elements: [] }, {}) === null
      && buildLegacyVtk(null, {}) === null);
check('校验函数直接可用',
      validateExportMesh(mesh) === null && validateExportMesh(null) !== null
      && validateExportFields(mesh, fields) === null);
check('没有结果场时说清楚是先求解',
      (describeExportProblem(mesh, {}) || '').includes('求解'),
      describeExportProblem(mesh, {}));
check('数据可用时没有问题描述', describeExportProblem(mesh, fields) === null);

// --- 文件名 -------------------------------------------------------------------
check('去掉几何扩展名', exportFilename('零件1.STEP', 'result', 'csv') === '零件1_result.csv',
      exportFilename('零件1.STEP', 'result', 'csv'));
check('路径被砍掉（不把目录写进文件名）',
      exportFilename('../../etc/passwd.step', 'result', 'vtk') === 'passwd_result.vtk',
      exportFilename('../../etc/passwd.step', 'result', 'vtk'));
check('Windows 反斜杠路径同样处理',
      exportFilename('C:\\tmp\\part.stl', 'result', 'csv') === 'part_result.csv',
      exportFilename('C:\\tmp\\part.stl', 'result', 'csv'));
check('非法字符换成下划线',
      exportFilename('a b:c*d?.step', 'result', 'csv') === 'a_b_c_d__result.csv',
      exportFilename('a b:c*d?.step', 'result', 'csv'));
check('空名字有兜底',
      exportFilename('', 'result', 'csv') === 'result_result.csv',
      exportFilename('', 'result', 'csv'));
check('后缀里的非法字符被去掉',
      exportFilename('p.step', 'a b/c', 'csv') === 'p_abc.csv',
      exportFilename('p.step', 'a b/c', 'csv'));
check('扩展名里的非法字符被去掉',
      exportFilename('p.step', 'result', 'c sv') === 'p_result.csv',
      exportFilename('p.step', 'result', 'c sv'));
check('时间戳格式固定',
      filenameStamp(new Date(2026, 2, 18, 14, 25, 30)) === '20260318-142530',
      filenameStamp(new Date(2026, 2, 18, 14, 25, 30)));

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_clip_plane(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/clipPlane.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _CLIP_PLANE_SELFTEST)
    return ok, ("平面方程符号 / 节点分类精确值 / 退化与非法输入 均符合断言"
                if ok else detail)


#: 剖切面的几何（纯函数）。
#:
#: 这是"剖切"里唯一能精确验证的部分，所以断言要写死数值：立方体 8 个顶点、
#: 平面切在正中 ⇒ 恰好 4 个节点被保留；符号方向错一格就会变成"剖掉全部"，
#: 而画面上只是"模型不见了"，很容易被当成别的问题。
#:
#: 也刻意钉住 `enabled=false` 的行为：**保留全部**（平面挪到包围盒之外），
#: 而不是"没有平面"——后者会让 three.js 因为剖切面数量变化而重编译着色器。
_CLIP_PLANE_SELFTEST = r"""
import { DEFAULT_CLIP_SPEC, classifyNodes, clipPosition, describeClip,
         modelBounds, planeDistance, planeEquation } from './clipPlane.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

// 单位立方体的 8 个顶点（0..1）
const cube = [
  [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
  [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
];

// --- 包围盒 -------------------------------------------------------------------
const bounds = modelBounds(cube);
check('包围盒', bounds.min.join(',') === '0,0,0' && bounds.max.join(',') === '1,1,1',
      JSON.stringify(bounds));
const offset = [[5, -2, 10], [7, -2, 10], [6, 3, 10]];
const offsetBounds = modelBounds(offset);
check('包围盒支持负数与非零原点',
      offsetBounds.min.join(',') === '5,-2,10' && offsetBounds.max.join(',') === '7,3,10',
      JSON.stringify(offsetBounds));
check('没有节点时返回 null',
      modelBounds([]) === null && modelBounds(null) === null);
check('节点里有非法值时返回 null（不编一个包围盒出来）',
      modelBounds([[0, 0, 0], [1, NaN, 1]]) === null
      && modelBounds([[0, 0], [1, 1, 1]]) === null);

// --- 平面位置 -----------------------------------------------------------------
check('fraction=0 在最负端', clipPosition(bounds, 'x', 0) === 0);
check('fraction=1 在最正端', clipPosition(bounds, 'x', 1) === 1);
check('fraction=0.5 在正中', clipPosition(bounds, 'x', 0.5) === 0.5);
check('fraction 被夹到 [0,1]',
      clipPosition(bounds, 'x', -3) === 0 && clipPosition(bounds, 'x', 9) === 1);
check('非法 fraction 当作 0',
      clipPosition(bounds, 'x', NaN) === 0 && clipPosition(bounds, 'x', 'x') === 0);
check('按轴取位置（z 轴）', clipPosition(offsetBounds, 'z', 0.5) === 10);

// --- 平面方程与符号 -----------------------------------------------------------
const below = planeEquation({ enabled: true, axis: 'x', fraction: 0.5, keepSide: 'below' }, bounds);
check('保留小侧：normal = -x', below.normal.join(',') === '-1,0,0' && below.constant === 0.5,
      JSON.stringify(below));
check('保留小侧：坐标小的为正距离',
      planeDistance(below, [0, 0.5, 0.5]) > 0 && planeDistance(below, [1, 0.5, 0.5]) < 0);

const above = planeEquation({ enabled: true, axis: 'x', fraction: 0.5, keepSide: 'above' }, bounds);
check('保留大侧：normal = +x', above.normal.join(',') === '1,0,0' && above.constant === -0.5,
      JSON.stringify(above));
check('保留大侧：坐标大的为正距离',
      planeDistance(above, [1, 0.5, 0.5]) > 0 && planeDistance(above, [0, 0.5, 0.5]) < 0);
check('两种方向互为相反数',
      planeDistance(below, [0.25, 0, 0]) === -planeDistance(above, [0.25, 0, 0]));

check('y / z 轴的 normal 位置正确',
      planeEquation({ enabled: true, axis: 'y', fraction: 0.5, keepSide: 'above' }, bounds)
        .normal.join(',') === '0,1,0'
      && planeEquation({ enabled: true, axis: 'z', fraction: 0.5, keepSide: 'below' }, bounds)
        .normal.join(',') === '0,0,-1');

// --- 节点分类：可以数出来的确切值 ---------------------------------------------
check('正中切开：恰好留一半',
      classifyNodes(cube, below).kept === 4 && classifyNodes(cube, below).removed === 4,
      JSON.stringify(classifyNodes(cube, below)));
check('留大侧也是 4 个', classifyNodes(cube, above).kept === 4);
check('保留比例', classifyNodes(cube, below).keptFraction === 0.5);
check('切在最负端：只剩该端面上的 4 个点',
      classifyNodes(cube, planeEquation(
        { enabled: true, axis: 'x', fraction: 0, keepSide: 'below' }, bounds)).kept === 4);
check('切在最正端：全留（距离为 0 算保留）',
      classifyNodes(cube, planeEquation(
        { enabled: true, axis: 'x', fraction: 1, keepSide: 'below' }, bounds)).kept === 8);
check('切在最正端留大侧：只剩该端面的 4 个点',
      classifyNodes(cube, planeEquation(
        { enabled: true, axis: 'x', fraction: 1, keepSide: 'above' }, bounds)).kept === 4);

// --- 不剖切时必须一个都不剖 ---------------------------------------------------
const off = planeEquation({ ...DEFAULT_CLIP_SPEC, enabled: false, axis: 'x' }, bounds);
const offCounts = classifyNodes(cube, off);
check('未启用时平面在包围盒之外', off.constant > bounds.max[0], String(off.constant));
check('未启用时全部保留（不是"没有平面"）',
      offCounts.kept === 8 && offCounts.removed === 0, JSON.stringify(offCounts));
check('未启用时 keptFraction = 1', offCounts.keptFraction === 1);
check('默认状态就是不剖切', DEFAULT_CLIP_SPEC.enabled === false);

// 退化包围盒（该轴跨度为 0）：仍然必须"全部保留"
const flat = [[0, 0, 0], [1, 0, 0]];
const flatPlane = planeEquation({ ...DEFAULT_CLIP_SPEC, enabled: false, axis: 'y' }, modelBounds(flat));
check('退化轴未启用时也全部保留',
      classifyNodes(flat, flatPlane).kept === 2, JSON.stringify(classifyNodes(flat, flatPlane)));

// --- 非法输入 -----------------------------------------------------------------
check('没有节点时分类不崩',
      classifyNodes(null, below).kept === 0
      && classifyNodes([], below).keptFraction === 1);
check('非法节点被跳过而不是算成保留',
      classifyNodes([[0, 0, 0], [NaN, 0, 0]], below).kept
      + classifyNodes([[0, 0, 0], [NaN, 0, 0]], below).removed === 1);
check('非法点算不出距离', planeDistance(below, [0, 'x', 0]) === null);

// --- 说明文本 -----------------------------------------------------------------
const text = describeClip(
  { enabled: true, axis: 'x', fraction: 0.5, keepSide: 'below' },
  0.5, classifyNodes(cube, below));
check('说明里带轴、位置、两侧节点数与百分比',
      text.includes('x = 0.5000') && text.includes('留下 4') && text.includes('剖掉 4')
      && text.includes('50.0%'),
      text);
check('未启用时没有说明',
      describeClip(DEFAULT_CLIP_SPEC, 0.5, classifyNodes(cube, off)) === null);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_runs_math(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/runsApi.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _RUNS_API_SELFTEST)
    return ok, ("列表映射 / 坏记录隔离 / 缺失≠0 / 网格与警告描述 均符合断言"
                if ok else detail)


#: 求解记录的读取与归一化（纯函数）。
#:
#: 重点钉三件事：
#:   1. **坏记录逐条丢弃**，不是整份失败——一条坏数据不该让"历史记录"整块消失，
#:      那正是用户最需要看到的证据；
#:   2. **缺失 ≠ 0**：应力真的可以是 0，所以非有限值必须被丢掉而不是写成 0；
#:   3. **摘要损坏要能标出来**：后端会返回 `summaryParseError`，界面据此提示，
#:      否则用户会把"空摘要"读成"这次什么都没算出来"。
_RUNS_API_SELFTEST = r"""
import { describeAnalysisType, describeRunMesh, describeRunWarnings,
         historyIsTrimmed, runQuantityNames, shouldShowGroup, toRun, toRunAnalysis,
         toRunList } from './runsApi.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const raw = {
  runs: [
    {
      id: 'run-1', projectId: 'p1', analysisType: 'structural', createdBy: 'alice',
      createdAt: '2026-03-18T06:00:00+00:00',
      summary: {
        quantities: { max_stress: 6.5e7, max_displacement: 2.2e-6 },
        mesh: { meshSize: 1.25, elements: 2735, nodes: 832 },
        warnings: ['忽略了一个约束'], warningCount: 1,
      },
      summaryParseError: false,
    },
    {
      id: 'run-2', projectId: 'p1', analysisType: 'modal', createdBy: null,
      createdAt: '2026-03-18T06:05:00+00:00',
      summary: {
        quantities: { first_elastic_frequency: 1234.5 },
        mesh: { elements: 100 }, warnings: [], warningCount: 0,
      },
      summaryParseError: false,
    },
  ],
  total: 2,
  limit: 50,
};

const list = toRunList(raw);
check('列表能解析', list.runs.length === 2 && list.total === 2 && list.limit === 50,
      JSON.stringify({ n: list.runs.length, total: list.total }));
check('数值原样保留',
      list.runs[0].summary.quantities.max_stress === 6.5e7
      && list.runs[0].summary.mesh.elements === 2735);
check('createdBy 可为 null', list.runs[1].createdBy === null);
check('考察量名字排序稳定',
      runQuantityNames(list.runs[0]).join(',') === 'max_displacement,max_stress',
      runQuantityNames(list.runs[0]).join(','));

// --- 坏记录逐条丢弃，不是整份失败 ---------------------------------------------
check('缺 id 的记录被丢弃', toRun({ createdAt: 'x' }) === null);
check('缺 createdAt 的记录被丢弃', toRun({ id: 'x' }) === null);
check('null / 非对象被丢弃',
      toRun(null) === null && toRun('x') === null && toRun(7) === null);
const mixed = toRunList({ ...raw, runs: [raw.runs[0], null, { id: 'bad' }, raw.runs[1]] });
check('坏记录被过滤，好记录保留', mixed.runs.length === 2,
      String(mixed.runs.length));
check('非数组 runs 得到空列表', toRunList({ runs: 'x' }).runs.length === 0);
check('垃圾输入得到空列表',
      toRunList(null).runs.length === 0 && toRunList('x').total === 0);

// --- 缺失 ≠ 0 -----------------------------------------------------------------
const dirty = toRun({
  id: 'r', createdAt: 'x',
  summary: { quantities: { max_stress: NaN, max_displacement: 0, bad: 'x', inf: Infinity } },
});
check('NaN / 字符串 / Infinity 被丢掉而不是写成 0',
      !('max_stress' in dirty.summary.quantities)
      && !('bad' in dirty.summary.quantities)
      && !('inf' in dirty.summary.quantities),
      JSON.stringify(dirty.summary.quantities));
check('真的是 0 的数值保留下来（0 是有意义的）',
      dirty.summary.quantities.max_displacement === 0,
      JSON.stringify(dirty.summary.quantities));
check('缺 summary 时不崩', toRun({ id: 'r', createdAt: 'x' }).summary.quantities
      && Object.keys(toRun({ id: 'r', createdAt: 'x' }).summary.quantities).length === 0);

// --- 摘要损坏要能标出来 -------------------------------------------------------
const corrupt = toRun({ id: 'r', createdAt: 'x', summaryParseError: true, summary: {} });
check('summaryParseError 透传', corrupt.summaryParseError === true);
check('默认不是损坏', toRun({ id: 'r', createdAt: 'x' }).summaryParseError === false);

// --- 描述文本 -----------------------------------------------------------------
check('网格信息三种都给',
      describeRunMesh(list.runs[0]) === '网格 1.25 · 2735 单元 · 832 节点',
      describeRunMesh(list.runs[0]));
check('缺网格字段时只显示有的',
      describeRunMesh(list.runs[1]) === '100 单元', describeRunMesh(list.runs[1]));
check('没有网格信息时返回空串',
      describeRunMesh(toRun({ id: 'r', createdAt: 'x' })) === '');
check('没有记录时描述为空', describeRunMesh(null) === '');

check('分析类型中文名',
      describeAnalysisType('structural') === '结构静力'
      && describeAnalysisType('thermal') === '稳态热传导'
      && describeAnalysisType('modal') === '模态',
      describeAnalysisType('structural'));
check('未知分析类型原样返回（不编造）',
      describeAnalysisType('cfd') === 'cfd' && describeAnalysisType(null) === '未知类型');

check('单条警告的措辞', describeRunWarnings(list.runs[0]).includes('1 条警告'),
      describeRunWarnings(list.runs[0]));
const manyWarnings = toRun({
  id: 'r', createdAt: 'x',
  summary: { quantities: {}, warnings: [], warningCount: 3 },
});
check('多条警告给出条数', describeRunWarnings(manyWarnings).includes('3 条警告'),
      describeRunWarnings(manyWarnings));
check('没有警告时不提示', describeRunWarnings(list.runs[1]) === null);
check('没有记录时不提示', describeRunWarnings(null) === null);

check('未达上限时不提示裁剪', historyIsTrimmed(list) === false);
check('达到上限时提示裁剪',
      historyIsTrimmed({ runs: [], total: 50, limit: 50 }) === true);
check('没有数据时不算裁剪', historyIsTrimmed(null) === false);

// --- 跨运行对比 ---------------------------------------------------------------
const analysis = toRunAnalysis({
  minRuns: 3,
  groups: [
    {
      analysisType: 'structural', setupSignature: 'sig-A', comparable: true,
      runCount: 4, distinctMeshCount: 3, quantity: 'max_stress',
      values: [29.02e6, 39.8e6, 55.65e6], sizes: [0.1328, 0.0902, 0.0473],
      runIds: ['a', 'b', 'c'],
      assessment: {
        observed_order: 0.31, extrapolated_limit: 1.31e8,
        last_relative_change: 0.19, converged: true,
      },
      verdict: '【事后对比，不是受控加密实验】观测收敛阶 0.31…',
    },
    {
      analysisType: 'structural', setupSignature: null, comparable: false,
      runCount: 2, distinctMeshCount: 2, quantity: 'max_stress',
      values: [1.0, 2.0], sizes: [1.0, 0.5], runIds: ['d', 'e'],
      assessment: null,
      verdict: '这些运行没有记录配置签名…不做收敛判断。',
    },
  ],
});
check('对比能解析', analysis.groups.length === 2 && analysis.minRuns === 3);
check('组字段映射（snake_case -> camelCase）',
      analysis.groups[0].observedOrder === 0.31
      && analysis.groups[0].extrapolatedLimit === 1.31e8
      && analysis.groups[0].lastRelativeChange === 0.19
      && analysis.groups[0].converged === true,
      JSON.stringify(analysis.groups[0]));
check('没有签名的组被标成不可比',
      analysis.groups[1].comparable === false
      && analysis.groups[1].observedOrder === null
      && analysis.groups[1].converged === false);
check('缺 assessment 时不崩（拿不到判定而不是编一个）',
      toRunAnalysis({ groups: [{ analysisType: 'x', values: [], sizes: [] }] })
        .groups[0].observedOrder === null);
check('坏组被丢弃，好组保留',
      toRunAnalysis({ groups: [null, 'x', analysis.groups[0]] }).groups.length === 1);
check('垃圾输入得到空对比',
      toRunAnalysis(null).groups.length === 0 && toRunAnalysis('x').minRuns === 3);
check('至少两个网格的组才值得显示', shouldShowGroup(analysis.groups[0]) === true);
check('只有一个网格的组不显示',
      shouldShowGroup({ ...analysis.groups[0], distinctMeshCount: 1 }) === false);
check('不可比的组仍然显示（要照实告诉用户）',
      shouldShowGroup(analysis.groups[1]) === true);
check('没有组时不显示', shouldShowGroup(null) === false);

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_frontend_result_export(node: str) -> tuple[bool, str]:
    """用 node 执行 `frontend/utils/resultExport.ts` 里的纯逻辑并断言其行为。"""
    ok, detail = _run_node_module_selftest(node, _RESULT_EXPORT_SELFTEST)
    return ok, ("CSV/VTK 结构 / 精确往返 / 不自洽时拒绝导出 / 文件名清洗 均符合断言"
                if ok else detail)


#: 用 node 把**真实求解结果**写成 CSV/VTK 文件（路径由环境变量给出）。
_EXPORT_REAL_RESULT_SCRIPT = r"""
import { readFileSync, writeFileSync } from 'node:fs';
import { buildLegacyVtk, buildResultCsv } from './resultExport.ts';

const payload = JSON.parse(readFileSync(process.env.SIMCLOUD_EXPORT_PAYLOAD, 'utf8'));
const fields = {
  vectors: [{ name: 'displacement_m', values: payload.displacements }],
  scalars: [{ name: 'von_mises_Pa', values: payload.stresses }],
};
const csv = buildResultCsv({ nodes: payload.nodes, elements: payload.elements }, fields);
const vtk = buildLegacyVtk({ nodes: payload.nodes, elements: payload.elements }, fields);
if (csv === null || vtk === null) {
  console.error('FAIL: 导出返回 null（数据被判定为不自洽）');
  process.exit(1);
}
writeFileSync(process.env.SIMCLOUD_EXPORT_CSV, csv, 'utf8');
writeFileSync(process.env.SIMCLOUD_EXPORT_VTK, vtk, 'utf8');
console.log('OK');
"""


def _parse_legacy_vtk(text: str) -> dict:
    """
    读回 VTK legacy ASCII 文件（结构化网格 + 逐节点场）。

    刻意手写而不是引入 vtk 库：这里要证明的是"**我们的导出确实是合法格式**"，
    用一个宽容的库来读会把格式错误掩盖掉。手写解析对格式的要求更硬
    （段名、计数、每行元素个数、CELL_TYPES 的值都要对）。
    """
    lines = text.split("\n")
    if not lines[0].startswith("# vtk DataFile Version"):
        raise ValueError(f"首行不是 VTK 头：{lines[0]!r}")
    if lines[2] != "ASCII" or lines[3] != "DATASET UNSTRUCTURED_GRID":
        raise ValueError(f"编码/数据集声明不对：{lines[2]!r} / {lines[3]!r}")

    index = 4
    parts = lines[index].split()
    if parts[0] != "POINTS":
        raise ValueError(f"缺少 POINTS 段：{lines[index]!r}")
    point_count = int(parts[1])
    index += 1
    points = []
    for _ in range(point_count):
        values = lines[index].split()
        if len(values) != 3:
            raise ValueError(f"坐标行不是三个数：{lines[index]!r}")
        points.append([float(value) for value in values])
        index += 1

    parts = lines[index].split()
    if parts[0] != "CELLS":
        raise ValueError(f"缺少 CELLS 段：{lines[index]!r}")
    cell_count, connectivity = int(parts[1]), int(parts[2])
    index += 1
    cells = []
    for _ in range(cell_count):
        values = lines[index].split()
        if int(values[0]) != 4 or len(values) != 5:
            raise ValueError(f"单元行不是「4 + 四个索引」：{lines[index]!r}")
        cells.append([int(value) for value in values[1:]])
        index += 1
    if connectivity != cell_count * 5:
        raise ValueError(f"连接表长度声明不符：{connectivity} != {cell_count * 5}")

    parts = lines[index].split()
    if parts[0] != "CELL_TYPES":
        raise ValueError(f"缺少 CELL_TYPES 段：{lines[index]!r}")
    type_count = int(parts[1])
    index += 1
    cell_types = [int(lines[index + offset]) for offset in range(type_count)]
    index += type_count

    result: dict = {
        "points": points,
        "cells": cells,
        "cell_types": cell_types,
        "vectors": {},
        "scalars": {},
    }

    if index < len(lines) and lines[index].startswith("POINT_DATA"):
        data_count = int(lines[index].split()[1])
        index += 1
        while index < len(lines) and lines[index].strip():
            parts = lines[index].split()
            if parts[0] == "VECTORS":
                name = parts[1]
                index += 1
                values = []
                for _ in range(data_count):
                    components = lines[index].split()
                    if len(components) != 3:
                        raise ValueError(f"矢量行不是三个数：{lines[index]!r}")
                    values.append([float(value) for value in components])
                    index += 1
                result["vectors"][name] = values
            elif parts[0] == "SCALARS":
                name = parts[1]
                index += 2          # SCALARS 行 + 必需的 LOOKUP_TABLE 行
                values = []
                for _ in range(data_count):
                    values.append(float(lines[index]))
                    index += 1
                result["scalars"][name] = values
            else:
                raise ValueError(f"未知的 POINT_DATA 子段：{parts[0]!r}")
    return result


def _check_result_export_roundtrip(
    node: str, mesh: dict, result: dict
) -> tuple[bool, str]:
    """
    真实结果 -> 前端导出 -> **Python 读回来逐位比对**。

    这是本轮最重要的检查，因为它同时钉住三件事：

    1. **精度**：文本里的数值必须能精确还原原始双精度数（用 `toFixed` 会静默丢精度）；
    2. **格式合法**：手写的 VTK 解析对段名/计数/每行元素个数都要求很硬，
       比引入一个宽容的 vtk 库更能暴露格式错误；
    3. **数据对应关系没错位**：节点数、单元索引、场的长度全部对齐——
       错位的文件"看起来完全正常"，只是把位移贴到了别的节点上。
    """
    import json
    import tempfile

    payload = {
        "nodes": mesh["nodes"],
        "elements": mesh["elements"],
        "displacements": result["displacements"],
        "stresses": result["stresses"],
    }

    with tempfile.TemporaryDirectory() as tmp:
        payload_path = os.path.join(tmp, "payload.json")
        csv_path = os.path.join(tmp, "result.csv")
        vtk_path = os.path.join(tmp, "result.vtk")
        with open(payload_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        ok, detail = _run_node_module_selftest(
            node,
            _EXPORT_REAL_RESULT_SCRIPT,
            {
                "SIMCLOUD_EXPORT_PAYLOAD": payload_path,
                "SIMCLOUD_EXPORT_CSV": csv_path,
                "SIMCLOUD_EXPORT_VTK": vtk_path,
            },
        )
        if not ok:
            return False, f"导出脚本失败：{detail}"

        with open(csv_path, encoding="utf-8", newline="") as handle:
            csv_text = handle.read()
        with open(vtk_path, encoding="utf-8") as handle:
            vtk_text = handle.read()

        try:
            parsed = _parse_legacy_vtk(vtk_text)
        except ValueError as exc:
            return False, f"VTK 读不回来：{exc}"

    node_count = len(payload["nodes"])
    cell_count = len(payload["elements"])

    # --- VTK ------------------------------------------------------------------
    if len(parsed["points"]) != node_count or len(parsed["cells"]) != cell_count:
        return False, (f"点数/单元数不符：{len(parsed['points'])}/{node_count}，"
                       f"{len(parsed['cells'])}/{cell_count}")
    if any(value != 10 for value in parsed["cell_types"]):
        return False, "CELL_TYPES 里出现了非四面体（10）"
    # 逐位比对：文本走的是最短往返表示，所以这里必须是**完全相等**而不是近似
    for index, node_coords in enumerate(payload["nodes"]):
        if [float(value) for value in node_coords] != parsed["points"][index]:
            return False, f"第 {index} 个节点坐标不一致"
    for index, cell in enumerate(payload["elements"]):
        if list(cell) != parsed["cells"][index]:
            return False, f"第 {index} 个单元连接不一致"
    vtk_displacements = parsed["vectors"].get("displacement_m")
    vtk_stresses = parsed["scalars"].get("von_mises_Pa")
    if vtk_displacements is None or vtk_stresses is None:
        return False, "VTK 里没有位移或应力场"
    for index, displacement in enumerate(payload["displacements"]):
        if [float(value) for value in displacement] != vtk_displacements[index]:
            return False, f"第 {index} 个节点的位移不一致"
    for index, stress in enumerate(payload["stresses"]):
        if float(stress) != vtk_stresses[index]:
            return False, f"第 {index} 个节点的应力不一致"

    # --- CSV ------------------------------------------------------------------
    lines = csv_text.split("\r\n")
    if lines[0] != "node,x,y,z,displacement_m_x,displacement_m_y,displacement_m_z,von_mises_Pa":
        return False, f"CSV 表头不符：{lines[0]!r}"
    if lines[-1] != "":
        return False, "CSV 没有以换行收尾"
    body = lines[1:-1]
    if len(body) != node_count:
        return False, f"CSV 数据行数 {len(body)} != 节点数 {node_count}"
    for index, line in enumerate(body):
        columns = line.split(",")
        if len(columns) != 8:
            return False, f"CSV 第 {index} 行列数不是 8"
        if int(columns[0]) != index:
            return False, f"CSV 第 {index} 行的节点索引是 {columns[0]}"
        expected_node = [float(value) for value in payload["nodes"][index]]
        if [float(value) for value in columns[1:4]] != expected_node:
            return False, f"CSV 第 {index} 行坐标不一致"
        expected_disp = [float(value) for value in payload["displacements"][index]]
        if [float(value) for value in columns[4:7]] != expected_disp:
            return False, f"CSV 第 {index} 行位移不一致"
        if float(columns[7]) != float(payload["stresses"][index]):
            return False, f"CSV 第 {index} 行应力不一致"
        # 同一个节点在 CSV 与 VTK 里必须一致（两条独立路径写同一份数据）
        if [float(value) for value in columns[4:7]] != vtk_displacements[index]:
            return False, f"CSV 与 VTK 在第 {index} 个节点上不一致"

    return True, (f"{node_count} 节点 / {cell_count} 单元，坐标·位移·应力"
                  f"在 CSV 与 VTK 两个文件里都与求解结果逐位一致")


#: 把**后端真实返回**的收敛检查结果喂给前端解析逻辑。
#:
#: 与网格质量、模态那两条同理：后端字段一旦改名（`last_relative_change` →
#: `lastRelativeChange`），界面只会静默地少显示一项，不报错、不影响任何数值。
#: 只有拿真实响应跑一遍前端的解析才能发现。
_CONVERGENCE_STUDY_DISPLAY_CHAIN_SELFTEST = r"""
import { readFileSync } from 'node:fs';
import { describeStudyStatus, describeStudySummary, studyRows,
         toConvergenceStudy, trendPoints } from './convergenceStudy.ts';

let failures = [];
const check = (name, ok, detail = '') => {
  if (!ok) failures.push(`${name}${detail ? ' -> ' + detail : ''}`);
};

const raw = JSON.parse(readFileSync(process.env.SIMCLOUD_STUDY_PAYLOAD, 'utf8'));
const study = toConvergenceStudy(raw);

check('真实响应能被前端解析（否则界面什么都不显示）', study !== null);
if (study) {
  check('分析类型一致', study.analysisType === raw.analysis_type, study.analysisType);
  check('级数一致', study.levels.length === raw.levels.length,
        `${study.levels.length} vs ${raw.levels.length}`);
  check('单元数逐级一致',
        study.levels.every((level, index) => level.elements === raw.levels[index].elements));
  check('主考察量在主考察量列表里', study.quantities.includes(study.primary),
        `${study.primary} in ${study.quantities}`);
  check('每个考察量都有判定',
        study.quantities.every(name => study.assessments[name] !== undefined),
        JSON.stringify(Object.keys(study.assessments)));
  check('逐级数值个数与级数一致',
        study.quantities.every(
          name => study.assessments[name].values.length === study.levels.length));
  check('四态之一是已知状态',
        ['converged', 'marginal', 'not-converged', 'insufficient']
          .includes(study.status) || study.status === 'insufficient',
        study.status);
  check('状态能翻译成界面文案',
        describeStudyStatus(study.status).label.length > 0);
  check('结论非空', study.verdict.length > 0);
  check('摘要能直接显示',
        describeStudySummary(study).includes(`${study.levels.length} 级网格`)
        || study.levels.length === 0,
        describeStudySummary(study));
  check('说明里带着「自收敛」这条限定',
        study.notes.some(note => note.includes('自收敛')),
        `${study.notes.length} 条说明`);
  check('说明里点出应力奇异',
        study.notes.some(note => note.includes('应力奇异')));
  check('每级的每个考察量都能格式化出非空文本',
        studyRows(study).every(row =>
          study.quantities.every(name => {
            const text = row.formatted[name];
            return typeof text === 'string' && text.length > 0;
          })));
  // 趋势图：有 2 级以上就应该能画，且横轴从 0 递增到 1
  if (study.levels.length >= 2) {
    const points = trendPoints(study, study.primary);
    check('趋势图点数与级数一致', points.length === study.levels.length,
          `${points.length} vs ${study.levels.length}`);
    check('趋势图横轴从 0 递增到 1',
          Math.abs(points[0].x) < 1e-9
          && Math.abs(points[points.length - 1].x - 1) < 1e-9
          && points.every((point, index) => index === 0 || point.x > points[index - 1].x),
          points.map(point => point.x.toFixed(3)).join(','));
  }
}

if (failures.length) {
  console.error('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
"""


def _check_convergence_study_display_chain(node: str, payload: dict) -> tuple[bool, str]:
    """把后端真实返回的收敛检查结果喂给前端解析逻辑（字段改名会在这里暴露）。"""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        path = handle.name

    try:
        ok, detail = _run_node_module_selftest(
            node,
            _CONVERGENCE_STUDY_DISPLAY_CHAIN_SELFTEST,
            {"SIMCLOUD_STUDY_PAYLOAD": path},
        )
    finally:
        os.unlink(path)

    return ok, (
        f"{payload.get('status')}，{len(payload.get('levels') or [])} 级 × "
        f"{len(payload.get('quantities') or [])} 个考察量，解析与作图均通过"
        if ok else detail
    )


def _check_mesh_quality_display_chain(node: str, payload: dict) -> tuple[bool, str]:
    """把后端真实返回的网格质量喂给前端解析逻辑（字段改名会在这里暴露）。"""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        path = handle.name

    try:
        ok, detail = _run_node_module_selftest(
            node,
            _MESH_QUALITY_DISPLAY_CHAIN_SELFTEST,
            {"SIMCLOUD_MESH_QUALITY_PAYLOAD": path},
        )
    finally:
        os.unlink(path)

    stats = payload.get("quality") or {}
    return ok, (f"{payload.get('elements', 0)} 单元，最低质量 {stats.get('min')}，"
                f"直方图与解析链均通过" if ok else detail)



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


#: 文档里"**当前**总数"的写法。
#:
#: 为什么不能简单匹配 `\d+ 项` / `\d+ 个用例`：文档里有**大量历史数字**
#: （"verify 新增 6 项"、"45 个用例、0.3 秒"…），那些是记录当时做了什么，
#: 不该等于今天的总数。所以只匹配固定的"当前总数"措辞，每个 pattern 绑死一份
#: 文件——这样漂移时能直接指出"是哪个文件的哪一句写了旧数字"。
_DOC_COUNT_PATTERNS = {
    "tests": (
        (r"(\d+)\s*个后端用例", "README.md"),
        (r"当前 \*\*(\d+)\*\* 个用例", "docs/01-开发流程与长期计划.md"),
        (r"\*\*(\d+)\*\*\s*个用例", "CONTRIBUTING.md"),
    ),
    "checks": (
        (r"(\d+)\s*项端到端检查", "README.md"),
        (r"\*\*(\d+)\*\*\s*项", "CONTRIBUTING.md"),
    ),
}


def documented_counts() -> dict:
    """读出文档里声明的"当前"用例数与检查项数（找不到的记为缺失）。"""
    import re

    found: dict = {"tests": [], "checks": []}
    for kind, entries in _DOC_COUNT_PATTERNS.items():
        for pattern, name in entries:
            path = ROOT / name
            if not path.exists():  # pragma: no cover - 文档被删了才可能发生
                found[kind].append((name, None))
                continue
            text = path.read_text(encoding="utf-8")
            matches = re.findall(pattern, text)
            if not matches:
                found[kind].append((name, None))
            else:
                for value in matches:
                    found[kind].append((name, int(value)))
    return found


def collect_test_count() -> int:
    """
    数一遍测试用例数，**只收集不执行**（收集只 import 模块，不跑测试体）。

    用它来校验文档里的用例数：验证活动本身不跑测试套件，所以这是最便宜的
    一致性检查手段。用 venv 的解释器；没有 venv（例如 CI）就用当前解释器——
    CI 里依赖是装在系统 Python 上的。
    """
    python = venv_python()
    if not python.exists():
        python = Path(sys.executable)
    completed = subprocess.run(
        [
            str(python), "-c",
            "import unittest;"
            "print(unittest.TestLoader().discover('tests', top_level_dir='.')"
            ".countTestCases())",
        ],
        cwd=str(BACKEND), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or "").strip().splitlines()[-1]
            if (completed.stderr or completed.stdout) else "收集用例失败"
        )
    return int(completed.stdout.strip().splitlines()[-1])


def task_verify(args: argparse.Namespace) -> int:
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

            passed, detail = _check_frontend_mesh_quality_math(node_bin)
            check("网格质量显示逻辑（缺失≠0，node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_convergence_study_math(node_bin)
            check("收敛检查显示逻辑（四态区分，node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_result_export(node_bin)
            check("结果导出（CSV/VTK 结构与精度，node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_runs_math(node_bin)
            check("求解记录显示逻辑（坏记录隔离，node 执行前端纯函数）", passed, detail)

            passed, detail = _check_frontend_clip_plane(node_bin)
            check("剖切面几何（平面符号与节点分类，node 执行前端纯函数）", passed, detail)
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

        # --- 网格质量：把"我的网格行不行"变成可对照的量 ---------------------------
        # 对照值是**解析体积**：default_cube.step 是 10×10×10 的立方体，体积恰好
        # 1000。单元体积之和只有在「节点映射、朝向修正、四面体体积公式」三者
        # 同时对的时候才等于它，所以这一段同时覆盖 /api/mesh-quality 与
        # fe_utils.load_tet_mesh_from_msh。
        #
        # 刻意**不**断言"结果有限/非 NaN"：那种断言在数量级错 1e6 倍时照样通过，
        # 本项目已经吃过这个亏（见 docs/03 面载荷精度那一条）。
        try:
            # default_cube.step 是**被 git 跟踪**的几何文件，而 POST /api/generate-cube
            # 会重新写出它——STEP 里带时间戳，字节必然不同。无条件调用会让每次
            # verify 都把工作区弄脏，并在提交里混进一个"几何看起来变了"的二进制
            # diff。所以只在缺失时才生成（新克隆的仓库本来就已经有这个文件）。
            if _http_status(
                "GET",
                f"{API_BASE}/api/geometry/default_cube.step/metadata",
                headers=api_headers,
            ) != 200:
                _http_json("POST", f"{API_BASE}/api/generate-cube", headers=api_headers)
            cube_mesh = _http_json(
                "POST",
                f"{API_BASE}/api/generate-mesh?filename=default_cube.step&mesh_size=1.5",
                headers=api_headers,
            )
            quality = _http_json(
                "GET",
                f"{API_BASE}/api/mesh-quality?filename=default_cube.step",
                headers=api_headers,
            )
            check(
                "网格质量：单元体积之和 = 立方体解析体积 1000",
                abs(quality["total_volume"] - 1000.0) < 1e-6,
                f"calc={quality['total_volume']:.9f} expect=1000",
            )
            check(
                "网格质量：无退化或朝向错误的单元",
                quality["non_positive_volume_count"] == 0
                and quality["non_finite_count"] == 0,
                f"非正体积={quality['non_positive_volume_count']} "
                f"非有限={quality['non_finite_count']}",
            )
            check(
                "网格质量：形状质量落在 (0, 1]",
                0.0 < quality["quality"]["min"]
                and quality["quality"]["max"] <= 1.0 + 1e-12,
                f"min={quality['quality']['min']:.4f} max={quality['quality']['max']:.4f} "
                f"mean={quality['quality']['mean']:.4f}",
            )
            # 直方图必须覆盖全部单元：被丢掉的往往是畸形单元，而那正是最需要被看到的
            histogram_total = sum(bin_["count"] for bin_ in quality["histogram"])
            check(
                "网格质量：直方图计数之和 = 单元数",
                histogram_total == quality["elements"] and quality["elements"] > 0,
                f"{histogram_total}/{quality['elements']}",
            )
            # 两个端点各自读同一个 .msh，单元/节点数必须一致（否则其中一条读取路径有问题）
            check(
                "网格质量：单元/节点数与生成网格接口一致",
                quality["elements"] == len(cube_mesh["elements"])
                and quality["nodes"] == len(cube_mesh["nodes"]),
                f"{quality['elements']} 单元 / {quality['nodes']} 节点",
            )
            # "形状好" != "网格够细"：结论里必须点出这一点，否则用户会把
            # 直方图好看直接当成结果可信（那是两件事，密度要看收敛性）。
            check(
                "网格质量：结论点出『形状好≠够细』",
                "收敛" in quality["verdict"],
                quality["verdict"],
            )
            check(
                "网格质量：未登录被拒",
                _http_status(
                    "GET", f"{API_BASE}/api/mesh-quality?filename=default_cube.step"
                ) == 401,
                "不带令牌应返回 401",
            )
            # 真实响应 -> 前端解析：字段改名只会让界面"静默地少显示一项"，
            # 造一份假数据是测不出来的（与模态那条同理）。
            if node_bin:
                passed, detail = _check_mesh_quality_display_chain(node_bin, quality)
                check("网格质量前端契约（真实响应对接解析逻辑）", passed, detail)
        except Exception as exc:
            check("网格质量检查", False, f"{type(exc).__name__}: {exc}")

        info("\n=== 5/7 求解器物理校准 ===")

        # --- 收敛阶基准：先确认这套离散确实按理论阶收敛 ---------------------------
        # 顺序是刻意的：如果离散格式本身不按理论阶收敛，后面"力对得上、热对得上"
        # 的断言都可能只是碰巧对上了。所以这一条排在所有物理量对照之前。
        #
        # mode=both 同时跑两种网格来源：
        #   structured 自相似 ⇒ 比值恰好是 2^p，可以按理论值收得很紧；
        #   pipeline 走真实路径（gmsh → load_tet_mesh_from_msh），非结构网格在前
        #   渐近区会偏离，容差放宽。
        # 用的是制造解 T = x² − y²（三维调和函数），精确解已知，所以量的是
        # **真实误差**而不是"两次结果的差"。约 11 秒。
        try:
            benchmark = _http_json(
                "POST",
                f"{API_BASE}/api/convergence/benchmark",
                {"mode": "both", "levels": 4},
                headers=api_headers,
            )

            def _assessment(payload: dict, mode: str):
                for item in payload.get("assessments") or []:
                    if item.get("mode") == mode:
                        return item
                return None

            structured_l2 = _assessment(benchmark["l2"], "structured")
            structured_h1 = _assessment(benchmark["h1"], "structured")
            pipeline_l2 = _assessment(benchmark["l2"], "pipeline")
            pipeline_h1 = _assessment(benchmark["h1"], "pipeline")

            check(
                "收敛基准：结果状态为 ok",
                benchmark["status"] == "ok",
                benchmark["verdict"],
            )
            check(
                "收敛基准：结构化网格 L2 阶 = 2（P1 单元）",
                structured_l2 is not None
                and abs(structured_l2["observed_order"] - 2.0) < 0.05,
                f"观测阶 {structured_l2 and structured_l2['observed_order']:.4f}",
            )
            check(
                "收敛基准：结构化网格 H1 阶 = 1",
                structured_h1 is not None
                and abs(structured_h1["observed_order"] - 1.0) < 0.05,
                f"观测阶 {structured_h1 and structured_h1['observed_order']:.4f}",
            )
            # 真实网格路径：阶数必须接近理论值（前渐近，容差放宽）
            check(
                "收敛基准：真实网格流水线 L2 阶趋近 2",
                pipeline_l2 is not None
                and 1.75 <= pipeline_l2["observed_order"] <= 2.05,
                f"观测阶 {pipeline_l2 and pipeline_l2['observed_order']:.4f}",
            )
            check(
                "收敛基准：真实网格流水线 H1 阶趋近 1",
                pipeline_h1 is not None
                and 0.85 <= pipeline_h1["observed_order"] <= 1.15,
                f"观测阶 {pipeline_h1 and pipeline_h1['observed_order']:.4f}",
            )
            # 误差必须**逐级下降**：只看首末两级会被中间的抖动骗过去。
            # 序列直接从 assessment["values"] 取——`benchmark["levels"]` 里两种
            # 模式的级别是前后排在一起的，按模式分开取才不会混。
            for name, key in (("L2", "l2"), ("H1", "h1")):
                for mode in ("structured", "pipeline"):
                    assessment = _assessment(benchmark[key], mode)
                    series = assessment and assessment.get("values")
                    if not series:
                        continue
                    check(
                        f"收敛基准：{mode} 的 {name} 误差逐级下降",
                        all(b < a for a, b in zip(series, series[1:])),
                        " → ".join(f"{value:.4g}" for value in series),
                    )

            bad_mode = _http_status(
                "POST",
                f"{API_BASE}/api/convergence/benchmark",
                {"mode": "nope"},
                headers=api_headers,
            )
            check(
                "收敛基准：非法 mode 被拒（400）",
                bad_mode == 400,
                f"HTTP {bad_mode}",
            )
            check(
                "收敛基准：未登录被拒",
                _http_status(
                    "POST", f"{API_BASE}/api/convergence/benchmark", {"mode": "structured"}
                ) == 401,
                "不带令牌应返回 401",
            )
            # 基准会临时生成一份几何副本；用完必须删干净，否则会在仓库里
            # 留下未跟踪文件（上一轮 POST /api/generate-cube 就是这么埋的坑）
            leftover = _http_status(
                "GET",
                f"{API_BASE}/api/geometry/benchmark_cube.step/metadata",
                headers=api_headers,
            )
            check(
                "收敛基准：临时几何已清理",
                leftover == 404,
                f"benchmark_cube.step -> HTTP {leftover}",
            )
        except Exception as exc:
            check("收敛基准", False, f"{type(exc).__name__}: {exc}")

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

            # --- 结果导出：真实结果 -> 前端生成文件 -> Python 读回来逐位比对 ----
            # 这是"导出"这件事唯一值得信任的验证方式：精度（最短往返表示）、
            # 格式合法性（手写 VTK 解析对段名/计数要求很硬）、以及数据对应关系
            # （错位的文件看起来完全正常，只是把位移贴到了别的节点上）。
            if node_bin:
                passed, detail = _check_result_export_roundtrip(node_bin, mesh, result)
                check("结果导出（真实结果 -> CSV/VTK -> Python 读回逐位比对）",
                      passed, detail)

            # --- 用户模型的 h-收敛检查 ------------------------------------------
            # 上面那条基准证明的是"这套离散会按理论阶收敛"，是**格式**的性质；
            # 这一条证明的是"**这个模型**的结果随加密稳定下来"，是**模型**的性质。
            # 两者缺一不可：格式收敛不等于你的网格够细。
            #
            # 用同一份配置（body）跑四级。注意它划的是几何的**临时副本**——
            # 若残留会在仓库里变成未跟踪文件，所以下面专门查一次。
            #
            # 用 4 级而不是 3 级：3 级常常还停在前渐近区（实测 3/1.5/0.75 时
            # 判为"未进入收敛区"，补到 4 级就正常了）。这也是接口的默认值。
            study = _http_json(
                "POST",
                f"{API_BASE}/api/convergence/study",
                {
                    "analysis_type": "structural",
                    "setup": dict(body),
                    "levels": 4,
                    "base_mesh_size": 3.0,
                },
                headers=api_headers,
            )
            counts = [level["elements"] for level in study["levels"]]
            notes_text = " ".join(study["notes"])
            check(
                "收敛检查：跑满 4 级且单元数逐级递增",
                len(study["levels"]) == 4 and counts == sorted(counts)
                and counts[0] < counts[-1],
                f"单元数 {counts}",
            )
            check(
                "收敛检查：每个考察量都有逐级数值与判定",
                set(study["quantities"]) == set(study["assessments"])
                and all(
                    len(study["assessments"][name]["values"]) == len(study["levels"])
                    for name in study["quantities"]
                ),
                f"{study['quantities']} -> status={study['status']}",
            )
            # 实测加密比必须被报出来：gmsh 的 mesh_size 只是名义目标，真实网格
            # 不按它成比例加密（实测 1.47/1.91/1.92，不是 2.00）。不说这一点，
            # 用户看到"差值没变小"只会以为自己的模型有问题。
            check(
                "收敛检查：按实测加密比估阶（而非名义的 2.00）",
                all(
                    study["assessments"][name].get("order_estimator") == "generalized"
                    for name in study["quantities"]
                )
                and "实际加密比" in notes_text,
                notes_text.split("实际加密比")[-1][:44] if "实际加密比" in notes_text
                else "说明里没有实测加密比",
            )
            # 这个功能最容易犯的错：拿"两次结果接近"当成"结果可信"。
            # 所以结论或说明里必须一直带着这条限定。
            check(
                "收敛检查：带着『自收敛≠模型正确』的限定",
                "不代表模型" in study["verdict"] or "不能" in notes_text,
                study["verdict"],
            )
            check(
                "收敛检查：说明里点出应力奇异与临时副本",
                "应力奇异" in notes_text and "临时副本" in notes_text,
                f"{len(study['notes'])} 条说明",
            )
            # 级数不足时不许硬下结论（这里显式只给 2 级，应当被 400 拒绝）
            too_few = _http_status(
                "POST",
                f"{API_BASE}/api/convergence/study",
                {
                    "analysis_type": "structural",
                    "setup": dict(body),
                    "mesh_sizes": [3.0, 1.5],
                },
                headers=api_headers,
            )
            check(
                "收敛检查：级数不足被拒（400）",
                too_few == 400,
                "两级结果接近可能是收敛，也可能是两处错得一样",
            )
            # 真实响应 -> 前端解析：字段改名只会让界面"静默地少显示一项"，
            # 造一份假数据是测不出来的（与网格质量、模态那两条同理）。
            if node_bin:
                passed, detail = _check_convergence_study_display_chain(node_bin, study)
                check("收敛检查前端契约（真实响应对接解析与作图）", passed, detail)

            # 异步路径：这个接口要跑 3~4 次"划网格 + 求解"，同步实现对大模型
            # 必然超时，所以必须能用 /api/jobs/convergence 提交并轮询到结果。
            submitted = _http_json(
                "POST",
                f"{API_BASE}/api/jobs/convergence",
                {
                    "analysis_type": "structural",
                    "setup": dict(body),
                    "levels": 3,
                    "base_mesh_size": 4.0,
                },
                headers=api_headers,
            )
            study_job: dict = {}
            for _ in range(120):
                time.sleep(0.5)
                study_job = _http_json(
                    "GET", f"{API_BASE}/api/jobs/{submitted['job_id']}",
                    headers=api_headers,
                )
                if study_job.get("status") in ("succeeded", "failed"):
                    break
            job_study = study_job.get("result") or {}
            check(
                "收敛检查：异步任务路径可用（提交 -> 轮询 -> 结果）",
                study_job.get("status") == "succeeded"
                and job_study.get("status") in ("converged", "marginal", "not-converged")
                and len(job_study.get("levels") or []) == 3,
                f"job={study_job.get('status')} study={job_study.get('status')}",
            )

            # 临时几何/网格必须删干净：残留会在 `git status` 里变成未跟踪文件
            # （上一轮 POST /api/generate-cube 重写被跟踪的 STEP 就是这么埋的坑）
            uploads = BACKEND / "uploads"
            if uploads.exists():
                leftovers = sorted(path.name for path in uploads.glob("conv_*"))
                check(
                    "收敛检查：临时文件已清理",
                    not leftovers,
                    f"残留 {leftovers}" if leftovers else "uploads/ 里没有 conv_* 残留",
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

            # --- 求解记录（run history）---------------------------------------
            # 它服务两件事：被共享的人能看到"属主算过什么、什么网格、结果多少"，
            # 以及跨网格/跨参数对比结果（收敛检查的自然下一步）。
            # 权限沿用项目那一套：读 = can_read，写 = can_edit。
            recorded = _http_json(
                "POST",
                f"{API_BASE}/api/projects/{created_id}/runs",
                {
                    "analysisType": "structural",
                    "quantities": {"max_stress": 6.5e7, "max_displacement": 2.2e-6},
                    "meshSize": 1.25,
                    "elements": 2735,
                    "nodes": 832,
                    "warnings": [],
                },
                headers=alice_headers,
            )
            check(
                "记录一次运行（属主）",
                recorded.get("id")
                and recorded.get("projectId") == created_id
                and recorded.get("summary", {}).get("quantities", {}).get("max_stress")
                == 6.5e7,
                f"id={recorded.get('id')} createdAt={recorded.get('createdAt')}",
            )
            history = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/runs",
                headers=alice_headers,
            )
            check(
                "历史记录里能读回同一条数值",
                history.get("total") == 1
                and history["runs"][0]["id"] == recorded["id"]
                and history["runs"][0]["summary"]["quantities"]["max_displacement"]
                == 2.2e-6,
                f"total={history.get('total')}",
            )
            check(
                "被共享者能看到历史（这正是共享的意义）",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/runs",
                    headers=bob_headers,
                ) == 200,
                "只读也要能看属主算过什么",
            )
            check(
                "只读者不能记也不能删（403）",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/runs",
                    {"analysisType": "structural",
                     "quantities": {"max_stress": 1.0, "max_displacement": 1.0}},
                    headers=bob_headers,
                ) == 403
                and _http_status(
                    "DELETE",
                    f"{API_BASE}/api/projects/{created_id}/runs/{recorded['id']}",
                    headers=bob_headers,
                ) == 403,
                "记录属于项目内容，写权与配置一致",
            )
            check(
                "不存在的项目：读与写都是 404",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/no-such-project/runs",
                    headers=alice_headers,
                ) == 404
                and _http_status(
                    "POST", f"{API_BASE}/api/projects/no-such-project/runs",
                    {"analysisType": "structural",
                     "quantities": {"max_stress": 1.0, "max_displacement": 1.0}},
                    headers=alice_headers,
                ) == 404,
                "不存在与无权一律 404，不泄露存在性",
            )
            check(
                "未登录访问运行记录被拒（401）",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/runs"
                ) == 401,
                "与其它计算类端点一致",
            )
            # 拼错的考察量必须被拒：静默接受会变成"历史里这一项一直是空的"
            check(
                "拼错的考察量被拒（400，而不是静默存下来）",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/runs",
                    {"analysisType": "structural",
                     "quantities": {"max_stress": 1.0, "max_displacement": 1.0,
                                    "max_strss": 9.9}},
                    headers=alice_headers,
                ) == 400,
                "多余/缺失的键都会被拒",
            )
            # NaN 不是合法 JSON：前端 JSON.parse 会直接抛错、整个列表打不开
            check(
                "非有限值被拒（400）",
                _http_status(
                    "POST", f"{API_BASE}/api/projects/{created_id}/runs",
                    {"analysisType": "structural",
                     "quantities": {"max_stress": float("nan"), "max_displacement": 1.0}},
                    headers=alice_headers,
                ) == 400,
                "NaN 写进 JSON 会让浏览器解析失败",
            )
            check(
                "删除记录（属主）",
                _http_status(
                    "DELETE",
                    f"{API_BASE}/api/projects/{created_id}/runs/{recorded['id']}",
                    headers=alice_headers,
                ) == 204
                and _http_json(
                    "GET", f"{API_BASE}/api/projects/{created_id}/runs",
                    headers=alice_headers,
                ).get("total") == 0,
                "删完 total 归零",
            )

            # --- 跨运行对比（配置签名是"可比较"的前提）-------------------------
            # 同一套配置、逐级加密的三次运行：应当被认出来是一条收敛序列。
            signature = "verify-signature-1"
            # 真按二阶收敛的数值（u = u* + C·N^(-2/3)，h² 对应单元数的 -2/3 次），
            # 这样打印出来的观测阶本身就是证据，而不是"能跑通"而已。
            expected_stress = 7.0e7
            for elements in (1000, 8000, 64000):
                stress = expected_stress + 3.0e7 * elements ** (-2 / 3)
                _http_json(
                    "POST",
                    f"{API_BASE}/api/projects/{created_id}/runs",
                    {
                        "analysisType": "structural",
                        "quantities": {"max_stress": stress,
                                       "max_displacement": 1.0e-6},
                        "elements": elements,
                        "setupSignature": signature,
                    },
                    headers=alice_headers,
                )
            # 另一套配置（不同签名）：**不能**混进上面那条序列
            _http_json(
                "POST",
                f"{API_BASE}/api/projects/{created_id}/runs",
                {
                    "analysisType": "structural",
                    "quantities": {"max_stress": 9.9e7, "max_displacement": 2.0e-6},
                    "elements": 500,
                    "setupSignature": "verify-signature-2",
                },
                headers=alice_headers,
            )
            comparison = _http_json(
                "GET", f"{API_BASE}/api/projects/{created_id}/runs/analysis",
                headers=alice_headers,
            )
            groups = comparison.get("groups") or []
            by_signature = {group.get("setupSignature"): group for group in groups}
            check(
                "跨运行对比：按配置签名分成两组（不同配置不能混成一条序列）",
                len(groups) == 2
                and set(by_signature) == {signature, "verify-signature-2"},
                f"{len(groups)} 组：{sorted(key for key in by_signature if key)}",
            )
            first = by_signature.get(signature) or {}
            first_assessment = first.get("assessment") or {}
            check(
                "跨运行对比：同签名的 3 次运行被判为二阶收敛",
                first.get("comparable") is True
                and first.get("distinctMeshCount") == 3
                and abs((first_assessment.get("observed_order") or 0.0) - 2.0) < 0.01
                and first.get("sizes") == sorted(first["sizes"], reverse=True),
                f"观测阶={first_assessment.get('observed_order')} "
                f"外推极限={first_assessment.get('extrapolated_limit')} "
                f"（真值 {expected_stress:.3g}）",
            )
            check(
                "跨运行对比：数值按粗 → 细单调且逐级更接近真值",
                len(first.get("values") or []) == 3
                and first["values"][0] > first["values"][1] > first["values"][2]
                and abs(first["values"][2] - expected_stress)
                < abs(first["values"][0] - expected_stress)
                and "事后对比" in (first.get("verdict") or ""),
                f"values={['%.4g' % v for v in (first.get('values') or [])]}",
            )
            check(
                "跨运行对比：只读也能看（被共享者需要看到这个结论）",
                _http_status(
                    "GET", f"{API_BASE}/api/projects/{created_id}/runs/analysis",
                    headers=bob_headers,
                ) == 200,
                "对比是读操作",
            )
            # 没有签名的旧记录：照实显示，但**拒绝判定**（不猜配置相同）
            legacy_id = _http_json(
                "POST", f"{API_BASE}/api/projects/{created_id}/runs",
                {
                    "analysisType": "thermal",
                    "quantities": {"max_heat_flux": 5.0e5, "max_temperature": 373.15},
                    "elements": 1000,
                },
                headers=alice_headers,
            )
            legacy_groups = [
                group for group in (
                    _http_json(
                        "GET", f"{API_BASE}/api/projects/{created_id}/runs/analysis",
                        headers=alice_headers,
                    ).get("groups") or []
                )
                if group.get("analysisType") == "thermal"
            ]
            check(
                "跨运行对比：没有配置签名的记录拒绝判定（不猜）",
                legacy_id.get("setupSignature") is None
                and len(legacy_groups) == 1
                and legacy_groups[0].get("comparable") is False
                and "不做收敛判断" in legacy_groups[0].get("verdict", ""),
                legacy_groups[0].get("verdict", "")[:40] if legacy_groups else "没有组",
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

    # --- 文档计数不能漂移 -----------------------------------------------------
    # 三份文档都写着"当前多少个用例、多少项检查"，而它们漂移过好几次
    # （CONTRIBUTING 里曾长期留着「84 项」，实际早已过百）。靠"记得同步"是
    # 靠不住的，所以做成自动闸门：**所有声明值必须等于实测值**，不一致就列出
    # 具体是哪个文件写了什么，而不是让人自己去找。
    #
    # 这个 check 是最后一条，因此"文档里的检查项数"应当等于 `len(checks) + 1`
    # （把这条自己算进去）。
    try:
        expected_checks = len(checks) + 1
        mismatches: list = []
        documented_checks = documented_counts()["checks"]
        for name, value in documented_checks:
            if value != expected_checks:
                mismatches.append(
                    f"{name} 写的检查项数是 {value if value is not None else '（缺失）'}"
                    f"，实际 {expected_checks}"
                )
        try:
            measured_tests = collect_test_count()
            for name, value in documented_counts()["tests"]:
                if value != measured_tests:
                    mismatches.append(
                        f"{name} 写的用例数是 {value if value is not None else '（缺失）'}"
                        f"，实际 {measured_tests}"
                    )
            tests_note = f"{measured_tests} 个用例"
        except Exception as exc:  # noqa: BLE001 - 数不出来时只报检查项数
            tests_note = f"用例数未能统计（{exc}）"
        check(
            "文档里的计数与实测一致",
            not mismatches,
            "；".join(mismatches) if mismatches
            else f"{tests_note} / {expected_checks} 项检查",
        )
    except Exception as exc:  # noqa: BLE001 - 这个闸门自己不许把 verify 弄崩
        check("文档里的计数与实测一致", False, f"{type(exc).__name__}: {exc}")

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
