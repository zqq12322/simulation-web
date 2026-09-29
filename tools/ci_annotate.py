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

#: 两种失败标记都要认：
#: - unittest 的 `FAIL: 用例名 (模块.类)`
#: - verify 自己的 `  [FAIL] 检查名   说明`
#: 第一版只认前一种，于是 verify 那一半的注解**什么也抓不到**——接线检查
#: （读自己的 workflow）才发现的。摘要行与失败行在 verify 里是同一行，
#: 所以 group(1) 会把两者都带上，这是可以接受的。
_FAILURE = re.compile(r"^(?:\[FAIL\]|(FAIL|ERROR):)\s*(.+)$")


def escape(text: str) -> str:
    for raw, encoded in _ESCAPES:
        text = text.replace(raw, encoded)
    return text


def failure_lines(text: str) -> list:
    """
    抽出 (失败用例/检查名, 说明) 对，**两种格式都认**：

    - unittest：`FAIL: 用例名 (模块.类)`，说明在分隔线之后的第一行（unittest 在那里
      打印用例首行 docstring，而本仓库把它当"这条在检查什么"来写）；
    - verify：`  [FAIL] 检查名   说明`，两者在同一行。
    """
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        match = _FAILURE.match(line.strip())
        if not match:
            continue
        if match.group(1):                      # unittest 形式：说明在下面几行
            detail = ""
            for candidate in lines[index + 1: index + 6]:
                stripped = candidate.strip()
                if not stripped or set(stripped) <= {"-", "="}:
                    continue                    # 分隔线 / 空行：继续往下找
                if stripped.startswith("Traceback"):
                    break                       # 说明在 Traceback 之前；没有就算了
                detail = stripped
                break
            found.append((match.group(2).strip(), detail))
        else:                                   # verify 形式：整行就是全部信息
            found.append((match.group(2).strip(), ""))
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
