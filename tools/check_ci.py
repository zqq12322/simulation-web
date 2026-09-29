"""
推送前审计：把"CI 会在哪一步红"里**本地能查的部分**都查一遍。

用法：

    python3 tools/check_ci.py

## 为什么要它

`origin/main` 至今没推过，所以 CI 从来没在 GitHub 上真跑过。我提醒过很多次"推上去
才能确认"，但"提醒"不是工程手段——能在这里查的就不该留给运气。CI 的第一步（装依赖）
尤其值得查：**本地永远复现不了**它的失败，因为本地依赖早就装好了，pip 只会说
"Requirement already satisfied"。

## 查什么

1. **每个 pin 在 PyPI 上真的存在**（requirements.txt + requirements.lock.txt）。
   版本号写错、被 yank、名字拼错 → CI 第一步就红。
2. **`package-lock.json` 与 `package.json` 同步**（`npm ci --dry-run`）。
   不同步的话 `npm ci` 直接 EUSAGE 退出——这是 npm 的一个著名硬失败。
3. **workflow 引用到的路径都存在，而且都进了版本库**。
   干净检出里没有未跟踪文件，所以"CI 引用了一个只有本机才有的文件"会在这里被抓住。
4. 如实列出**本地查不了的部分**（见 `UNCHECKABLE`），而不是让它们看起来被验过了。
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = (
    ROOT / "backend" / "requirements.txt",
    ROOT / "backend" / "requirements.lock.txt",
)

#: 本地查不了的部分。**必须如实列出来**，否则"审计通过"会被误读成"CI 一定会过"。
UNCHECKABLE = (
    "workflow 里 bash 片段的语法（本机没有 bash）",
    "apt 包在 ubuntu-latest 上是否可得（需要真的 runner）",
    "runner 上的 Node / Python 版本是否满足要求（本机是 24 / 3.12）",
    "GitHub Actions 各 action 的可用性（setup-python@v5 等）",
)

_PIN = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*==\s*([^\s;#]+)")


def info(message: str) -> None:
    print(message)


def display(path: pathlib.Path) -> str:
    """
    尽量显示成相对路径；不在仓库内就显示绝对路径。

    不要直接用 `relative_to`：它对仓库外的路径会抛 `ValueError`——工具不该因为
    被指向一个临时目录就崩掉（自检/替换测试正会这么干）。
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def fail(message: str) -> None:
    print(f"  [问题] {message}")


# ------------------------------------------------------------------ 1) pins
def parse_pins(path: pathlib.Path) -> list:
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        match = _PIN.match(line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def pypi_has(name: str, version: str) -> tuple:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "simcloud-check-ci"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, "PyPI 上没有这个版本"
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 网络问题要如实报，不能算通过
        return False, f"{type(exc).__name__}: {exc}"
    if payload.get("info", {}).get("yanked", False):
        return True, "已被 yank（装得上，但不该用）"
    return True, ""


def check_pins() -> list:
    problems = []
    for path in REQUIREMENTS:
        if not path.exists():
            problems.append(f"找不到 {display(path)}")
            continue
        pins = parse_pins(path)
        bad = 0
        for name, version in pins:
            ok, note = pypi_has(name, version)
            if not ok:
                bad += 1
                problems.append(f"{name}=={version}（{note}）")
            elif note:
                info(f"  [警告] {name}=={version} {note}")
        info(f"  {display(path)}：{len(pins)} 个 pin，{len(pins) - bad} 个存在")
    return problems


# ------------------------------------------------------------------ 2) npm
def check_npm_lock() -> tuple:
    """返回 (problems, 是否真的查了)。"""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    frontend = ROOT / "frontend"
    if not npm:
        return [], False
    if not (frontend / "package-lock.json").exists():
        return ["frontend/package-lock.json 不存在"], True
    completed = subprocess.run(
        [npm, "ci", "--dry-run", "--no-audit", "--no-fund"],
        cwd=str(frontend), capture_output=True, text=True,
        encoding="utf-8", errors="replace", shell=False,
    )
    if completed.returncode == 0:
        info("  package-lock.json 与 package.json 同步")
        return [], True
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return [f"npm ci 校验失败：{detail[-1] if detail else '未知原因'}"], True


# ------------------------------------------------------------- 3) workflow
def git_tracked(relative: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return completed.returncode == 0


def git_available() -> bool:
    """当前目录是不是 git 工作树。不是的话，`git ls-files` 的结论没有意义。"""
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return completed.returncode == 0 and "true" in completed.stdout


def check_workflow() -> list:
    """
    离线可查的那部分：job 齐不齐、引用到的路径是否存在且**进了版本库**。

    干净检出里没有未跟踪文件，所以"CI 引用了一个只有本机才有的文件"会在这里被
    抓住。**不是 git 仓库时跳过跟踪断言**——否则从压缩包解出来的副本会误报。
    """
    problems = []
    if not WORKFLOW.exists():
        return ["找不到 .github/workflows/ci.yml"]
    text = WORKFLOW.read_text(encoding="utf-8")
    tracked_checkable = git_available()

    # 三个 job 必须都在（名字与 README/CONTRIBUTING 里说的一致）
    for job in ("backend:", "frontend:", "verify:"):
        if not re.search(rf"^  {re.escape(job)}", text, re.MULTILINE):
            problems.append(f"workflow 里少了 job：{job.rstrip(':')}")

    # 引用到的路径必须存在，而且**进了版本库**
    referenced = set(re.findall(r"working-directory:\s*(\S+)", text))
    referenced |= set(re.findall(r"cache-dependency-path:\s*(\S+)", text))
    referenced |= set(re.findall(r"([\w./-]*backend/requirements[\w.]*)", text))
    referenced |= set(re.findall(r"(tools/tasks\.py)", text))
    referenced |= set(re.findall(r"(frontend/package-lock\.json)", text))
    for relative in sorted(referenced):
        target = ROOT / relative
        if not target.exists():
            problems.append(f"workflow 引用了不存在的路径：{relative}")
        elif tracked_checkable and not git_tracked(relative):
            problems.append(f"workflow 引用的路径没有进版本库：{relative}（干净检出里没有）")
    suffix = "且在版本库中" if tracked_checkable else "（未跟踪断言已跳过：不是 git 仓库）"
    info(f"  workflow 引用 {len(referenced)} 个路径"
         + (f"，全部存在{suffix}" if not problems else ""))

    # 两个 job 的关键命令与文档里写的一致
    for needle in ("python -m unittest discover", "npx tsc --noEmit", "npm run build",
                   "tools/tasks.py verify"):
        if needle not in text:
            problems.append(f"workflow 里找不到这条命令：{needle}")
    return problems


def check_declared_dependencies() -> list:
    """
    后端源码/测试里 import 的第三方包，是否都在 `requirements.txt` 里声明了。

    **为什么这是 CI 专属的失败**：本机的 venv 是三十多轮里一点点长出来的，装过
    一堆没写进 requirements 的东西；那种代码在本机永远绿，而 CI 上
    `pip install -r requirements.txt` 装完就缺包——这正是"本地复现不了"的典型。

    （第一次 CI 运行就出现了这个形态：后端测试 job 在 12 秒内失败，而本地 513
    个用例全过。前端 job 与所有安装步骤都是成功的。）
    """
    import ast
    import sys
    from importlib.metadata import packages_distributions

    try:
        import importlib.metadata as metadata
    except ImportError:  # pragma: no cover - 3.8+ 都有
        return []

    declared = set()
    for path in REQUIREMENTS:
        for name, _version in parse_pins(path):
            declared.add(name.lower().replace("_", "-"))

    local_modules = {
        path.stem for path in (ROOT / "backend").glob("*.py")
    }
    mapping = packages_distributions()
    problems = []
    seen = set()
    targets = list((ROOT / "backend").glob("*.py"))
    targets += list((ROOT / "backend" / "tests").glob("*.py"))
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误由别的检查负责
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name in local_modules:
                    continue
                if name.startswith("tests") or name in seen:
                    continue
                distributions = mapping.get(name)
                if not distributions:
                    seen.add(name)
                    problems.append(
                        f"{path.name} 导入了 {name}，但本机没装它（无法判断归属）"
                    )
                    continue
                # 该模块由某个发行版提供；只要**任一**提供者在 requirements 里就算声明过
                if not any(
                    dist.lower().replace("_", "-") in declared
                    for dist in distributions
                ):
                    seen.add(name)
                    problems.append(
                        f"{path.name} 导入了 {name}（来自 {distributions[0]}），"
                        "但 requirements.txt 里没有声明它"
                    )
    if not problems:
        info("  后端源码/测试的第三方 import 都能在 requirements.txt 里找到")
    return problems


def main() -> int:
    problems = []

    info("=== 1/4 PyPI 上的 pinned 版本 ===")
    problems += check_pins()

    info("\n=== 2/4 npm lock 文件同步 ===")
    npm_problems, checked = check_npm_lock()
    problems += npm_problems
    if not checked:
        info("  跳过：本机没有 npm（CI 上这一步是 npm ci）")

    info("\n=== 3/4 依赖声明完整性（本地装了但没声明 = CI 上必挂）===")
    problems += check_declared_dependencies()

    info("\n=== 4/4 workflow 结构与引用 ===")
    problems += check_workflow()

    info("\n=== 本地查不了的部分（不算通过）===")
    for item in UNCHECKABLE:
        info(f"  - {item}")

    print()
    if problems:
        info(f"{len(problems)} 个问题：")
        for problem in problems:
            fail(problem)
        return 1
    info("本地能查的都对上了。**这不等于 CI 会过**——上面那些查不了的项只能等推上去。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
