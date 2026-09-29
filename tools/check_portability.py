"""
跨平台可移植性审计：专查"Windows 上开发、Linux 上必挂"的那一类问题。

用法：

    python3 tools/check_portability.py

## 为什么要它

这个项目在 Windows 上开发了三十多轮，而 CI 跑在 Linux 上。有一整类问题**在
Windows 上永远看不出来**：

1. **import 的大小写不匹配**（`import Config` 而文件是 `config.py`）。
   Windows 文件系统不敏感，Linux 敏感——`ModuleNotFoundError`。
   更阴的是：**同一目录下存在仅大小写不同的两个文件**，检出时就会互相覆盖。
2. **同一个目录里仅大小写不同的文件名**：Windows 上只能存在一个，而 Linux 上
   两个都在，import 到哪个取决于顺序。
3. **代码里写死的 Windows 路径分隔符**（`"uploads\\part.step"`）。
4. **按名字引用数据文件时大小写不一致**（`test_part.STEP` 而磁盘上是 `.step`）。

第一次 CI 运行的两个失败（后端测试 job 12 秒失败、verify job 的后端 60 秒起不来）
**可以由同一个原因解释**：某个 import 在 Linux 上失败，于是测试收集失败、应用也
起不来。而 `clone-verify` 是在 Windows 上克隆的，抓不到这类问题——这正是补这个
审计的理由。

这个检查**不需要联网、不需要起服务**，所以它同时进了 `verify`。
"""

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
TESTS = BACKEND / "tests"

#: 允许出现在 Python 字符串里的 Windows 路径写法（`\n` `\t` 之类不是路径，排除）
_WINDOWS_PATH = re.compile(r"[\"'][^\"']*[A-Za-z0-9_)\]]\\\\[A-Za-z0-9_]")

#: **真正**的文件系统调用。只有出现这些，反斜杠字面量才是危险信号：
#: 把它交给 `isSafeFilename()` / `resolve_upload_path()` 之类的校验器反而是**正确**
#: 用法（那些测试就是故意喂 Windows 路径进去，证明它会被拒绝）。
#: 第一版没有这一层，于是把 6 处"路径安全的测试输入"全报成了问题——
#: 一个总在报错的检查等于没有检查。
_FILESYSTEM_CALL = re.compile(
    r"\b(open|Path|read_text|write_text|read_bytes|write_bytes|mkdir|glob|rglob"
    r"|exists|iterdir|unlink|remove|rmtree|stat|resolve|loadtxt|savetxt)\s*\("
)


def local_modules() -> dict:
    """本地模块名 → 真实文件名（**保留大小写**）。"""
    modules = {}
    for path in BACKEND.glob("*.py"):
        modules.setdefault(path.stem, []).append(path.name)
    for path in TESTS.glob("*.py"):
        modules.setdefault(path.stem, []).append(f"tests/{path.name}")
    return modules


def check_import_case() -> list:
    problems = []
    modules = local_modules()
    lower_index = {}
    for name, files in modules.items():
        lower_index.setdefault(name.lower(), []).append((name, files))

    # 1) 仅大小写不同的**文件名**：Linux 上两个都存在，Windows 上只能有一个
    for lowered, entries in sorted(lower_index.items()):
        if len(entries) > 1:
            names = ", ".join(real for real, _ in entries)
            problems.append(
                f"存在仅大小写不同的文件/模块名：{names}"
                "（Windows 上只能保留一个，Linux 上会同时存在并互相覆盖）"
            )

    # 2) import 的名字与真实文件名大小写不一致
    targets = sorted(BACKEND.glob("*.py")) + sorted(TESTS.glob("*.py"))
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # 语法错误由 pyflakes / ast 检查负责
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:      # 相对 import，相对的是包，不在这里判
                    continue
                if node.module:
                    parts = node.module.split(".")
                    names = [parts[0]]
                    # `from tests.foo import x` 要连子模块一起查
                    if parts[0] == "tests" and len(parts) > 1:
                        names.append(parts[1])
                    # `from config import X` 里的 X 也可能是个模块名
                    names += [alias.name for alias in node.names]
            for name in names:
                entries = lower_index.get(name.lower())
                if not entries:
                    continue
                real_names = {real for real, _ in entries}
                if name not in real_names:
                    problems.append(
                        f"{path.name} 里 import 了 {name}，"
                        f"但磁盘上的名字是 {'/'.join(sorted(real_names))}"
                        "（Linux 大小写敏感：会 ModuleNotFoundError）"
                    )
    return sorted(set(problems))


def check_windows_paths() -> list:
    """
    写死的反斜杠路径**并且**用在了文件系统调用上。

    只报"给校验器当输入"的那种是错判：`isSafeFilename('a\\\\b.stl')` 期望的正是
    "被拒绝"，`resolve_upload_path("..\\\\secret.step")` 期望的正是抛 ValueError。
    所以要求同一行里出现真正的文件系统调用。
    """
    problems = []
    targets = sorted(BACKEND.glob("*.py")) + sorted(TESTS.glob("*.py"))
    targets += sorted((ROOT / "tools").glob("*.py"))
    for path in targets:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _WINDOWS_PATH.search(line) and _FILESYSTEM_CALL.search(line):
                problems.append(
                    f"{path.relative_to(ROOT)}:{number} 用反斜杠路径访问文件系统："
                    f"{stripped[:70]}"
                )
    return problems


def main() -> int:
    sections = (
        ("import / 文件名大小写", check_import_case()),
        ("用反斜杠路径访问文件系统", check_windows_paths()),
    )
    total = 0
    for title, problems in sections:
        print(f"=== {title} ===")
        if problems:
            total += len(problems)
            for problem in problems:
                print(f"  [问题] {problem}")
        else:
            print("  没有发现问题")
    print()
    print("=== 这一类**故意不查**（说明见模块 docstring）===")
    print("  按名字引用 uploads 下的文件时大小写不一致：")
    print("    同一个写法在一处是 bug、在另一处是**正确的探测器**——")
    print("    `(UPLOAD_DIR / \"零件1.step\").exists()` 正是判断文件系统是否大小写")
    print("    敏感的办法。这需要人的意图，工具判不了；已由 test_config.py 的")
    print("    双向断言覆盖。")
    print()
    if total:
        print(f"{total} 个跨平台问题：Windows 上看不出来，Linux（CI）上必挂")
        return 1
    print("没有发现跨平台问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
