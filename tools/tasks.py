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
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
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


def _face_by_normal(faces, axis: int, sign: int):
    """按真实法向挑面——不能假设 face id 1..6 就是 ±X/±Y/±Z。"""
    for face in faces:
        normal = face.get("normal")
        if normal and normal[axis] * sign > 0.9:
            return face
    raise AssertionError(f"未找到法向约为 {'+' if sign > 0 else '-'}{'XYZ'[axis]} 的面")


def task_verify(args: argparse.Namespace) -> int:
    """端到端验证 + 物理校准。等价于 docs 里描述的 11 项检查。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, passed, detail))
        marker = _c("PASS", "green") if passed else _c("FAIL", "red")
        print(f"  [{marker}] {name:<42} {detail}")

    if not args.skip_frontend:
        info("=== 1/4 前端类型检查 ===")
        node = shutil.which("node")
        if node:
            code = subprocess.run(
                [node, "node_modules/typescript/bin/tsc", "--noEmit"],
                cwd=str(FRONTEND),
            ).returncode
            check("TypeScript 类型检查", code == 0, f"exit={code}")
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
        except Exception as exc:
            check("求解器物理校准", False, f"{type(exc).__name__}: {exc}")

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
