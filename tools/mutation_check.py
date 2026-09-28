"""
变异测试：故意改坏前端纯函数，确认 node 自检**会红**。

用法（需要 node，不需要启动服务）：

    python3 tools/mutation_check.py

## 为什么需要它

`verify` 里的前端自检全绿有两种可能：

1. 代码是对的；
2. **断言没长牙**——写了 `check(...)` 但条件恒真。

这两种情况从输出上完全看不出来。变异测试区分它们：把实现改坏一点点，看自检
是否报错。

这不是理论担忧：第一次跑这个脚本就抓到一条没有牙的断言——把用户提示里的
"超过服务端保留上限"（**原因**）删掉，所有断言仍然通过。于是补上了
`说明里给出原因（超过上限）`。用户看到"未保留"却不知道原因，就无从判断该不该
重试；而测试当时完全没在看这件事。

## 注意

- **它会临时改动源文件**，跑完用 `try/finally` 还原；
- 因此**别在跑它的时候编辑那几个文件**，也别中途 Ctrl+C（SIGKILL 不会执行
  finally，源文件会停在变异状态——`git checkout` 一下即可复原）；
- 它**不进 CI**：这是人工使用的诊断工具，不是常规验证步骤。
"""

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import tasks  # noqa: E402  - 复用它的 node 自检执行器

NODE = shutil.which("node")

#: (文件, 自检常量名, 变异说明, 原文, 替换)
MUTATIONS = [
    (
        "frontend/utils/jobResult.ts", "_JOB_RESULT_SELFTEST",
        "判定只看 resultDropped，不看结果是否为空",
        "job.resultDropped === true && job.result === null",
        "job.resultDropped === true",
    ),
    (
        "frontend/utils/jobResult.ts", "_JOB_RESULT_SELFTEST",
        "体积非法时不兜底（界面会出现 NaN MB）",
        "if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes <= 0) {",
        "if (typeof bytes !== 'number') {",
    ),
    (
        "frontend/utils/jobResult.ts", "_JOB_RESULT_SELFTEST",
        "文案丢掉原因",
        "超过服务端保留上限，",
        "",
    ),
    (
        "frontend/utils/jobResult.ts", "_JOB_RESULT_SELFTEST",
        "文案丢掉下一步（不告诉用户怎么办）",
        "请减小网格规模（或降低模态阶数）后重试。",
        "",
    ),
    (
        "frontend/utils/jobResult.ts", "_JOB_RESULT_SELFTEST",
        "没有体积信息时也说 0 MB",
        "megabytes > 0 ?",
        "true ?",
    ),
    (
        "frontend/utils/projectSetup.ts", "_PROJECT_SETUP_SELFTEST",
        "冲突状态显示成已保存（用户会以为改动存下来了）",
        "    case 'conflict':\n      return '有冲突：未保存';",
        "    case 'conflict':\n      return '已保存';",
    ),
    (
        "frontend/utils/projectSetup.ts", "_PROJECT_SETUP_SELFTEST",
        "版本取不到时回落到 0（会绕开并发保护）",
        "    return null;\n  }\n  return value;",
        "    return 0;\n  }\n  return value;",
    ),
]


def run() -> int:
    if not NODE:
        print("找不到 node，无法运行变异测试")
        return 1

    missed = 0
    touched = sorted({name for name, *_ in MUTATIONS})
    for name, selftest, label, old, new in MUTATIONS:
        path = ROOT / name
        original = path.read_text(encoding="utf-8")
        if old not in original:
            print(f"!! 变异点没找到（代码变了，脚本要更新）：{label}")
            missed += 1
            continue
        path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
        try:
            passed, detail = tasks._run_node_module_selftest(
                NODE, getattr(tasks, selftest)
            )
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
        print(f"[{'抓住' if not passed else '**没抓住**'}] {label}")
        if not passed:
            print(f"           {detail[:120]}")
        else:
            missed += 1

    print()
    for name in touched:
        selftest = next(m[1] for m in MUTATIONS if m[0] == name)
        passed, detail = tasks._run_node_module_selftest(
            NODE, getattr(tasks, selftest)
        )
        state = "通过" if passed else f"失败 {detail[:80]}"
        print(f"还原后 {name}: {state}")

    if missed:
        print(
            f"\n{missed} 个变异没被抓住：说明对应的断言没有牙。"
            "**补断言，不要补实现**——断言才是这里的产物。"
        )
        return 1
    print(f"\n{MUTATIONS.__len__()} 个变异全部被抓住")
    return 0


if __name__ == "__main__":
    sys.exit(run())
