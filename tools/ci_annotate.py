"""
把 CI 的失败原因变成 GitHub 注解（`::error::`），这样**不用管理员权限也能看到**。

## 为什么需要它

Actions 的**日志下载**需要 admin 权限——公共仓库也一样（实测
`/actions/jobs/{id}/logs` 返回 `403 Must have admin rights`）。而**注解**可以
通过公开 API 读到：

    https://api.github.com/repos/<owner>/<repo>/check-runs/<id>/annotations

第一次 CI 运行失败时，我作为没有仓库权限的协作者**完全看不到失败原因**，只能猜：
"12 秒失败"和"60 秒超时"都是间接推断。把关键信息发成注解之后，"CI 红了但不知道
为什么"就不再取决于谁的权限高——这本身也是协作基础设施的一部分。

## 用法

    python3 tools/ci_annotate.py <文件> [--mode failures|tail] [--max 20] [--title 标题]

- `failures`：抽出 unittest / verify 的 `FAIL:` `ERROR:` 行（连同紧跟的一行——
  那是 unittest 打印的用例首行说明，往往直接写明了断言意图）；
- `tail`：最后 N 行（用于"后端起不来"这类需要看异常栈的场合）。

文件不存在时**安静退出 0**：注解是尽力而为的，不该把 CI 弄出新的失败。
"""

import argparse
import pathlib
import re
import sys

#: GitHub workflow command 里的转义规则
_ESCAPES = (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"))

_FAILURE = re.compile(r"^(FAIL|ERROR):\s*(.+)$")


def escape(text: str) -> str:
    for raw, encoded in _ESCAPES:
        text = text.replace(raw, encoded)
    return text


def failure_lines(text: str) -> list:
    """
    抽出 (失败用例, 说明) 对。

    说明取的是**分隔线之后的第一行**——unittest 在那里打印用例的首行 docstring，
    而这个仓库把 docstring 当"这条在检查什么"来写，所以它往往比用例名有用得多。
    （第一版只看紧跟失败行的下一行，结果那一行是 `------` 分隔线，说明全是空的。）
    """
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        match = _FAILURE.match(line.strip())
        if not match:
            continue
        detail = ""
        for candidate in lines[index + 1: index + 6]:
            stripped = candidate.strip()
            if not stripped or set(stripped) <= {"-", "="}:
                continue          # 分隔线 / 空行：继续往下找
            if stripped.startswith("Traceback"):
                break             # 说明行在 Traceback 之前；没有就算了
            detail = stripped
            break
        found.append((match.group(2).strip(), detail))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="把失败原因发成 GitHub 注解")
    parser.add_argument("path", help="要读的文件")
    parser.add_argument("--mode", choices=("failures", "tail"), default="failures")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--title", default="CI")
    args = parser.parse_args()

    path = pathlib.Path(args.path)
    if not path.exists():
        print(f"（没有 {path}，跳过注解）")
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")

    if args.mode == "tail":
        lines = [line for line in text.splitlines() if line.strip()]
        for line in lines[-args.max:]:
            print(f"::error title={escape(args.title)}::{escape(line.strip()[:400])}")
        return 0

    failures = failure_lines(text)
    if not failures:
        print(f"::warning title={escape(args.title)}::文件里没有 FAIL/ERROR 行（{path}）")
        return 0
    for name, detail in failures[: args.max]:
        message = name if not detail else f"{name} — {detail}"
        print(f"::error title={escape(args.title)}::{escape(message[:400])}")
    if len(failures) > args.max:
        print(
            f"::error title={escape(args.title)}::"
            f"另有 {len(failures) - args.max} 条失败未列出（--max {args.max}）"
        )
    print(f"共 {len(failures)} 条失败已发成注解")
    return 0


if __name__ == "__main__":
    sys.exit(main())
