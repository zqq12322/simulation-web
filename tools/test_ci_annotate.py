"""注解器自测：两种失败格式都要抓到，且不能抓错。

为什么单独测它：CI 失败时**注解是我唯一能读到的信息**（日志要 admin 权限）。
它抓不到就等于我又回到了"只看得到红、看不到为什么"。第一次写它时漏了
verify 的 `[FAIL]` 格式，接线检查才发现。
"""
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "ci_annotate.py"
PYTHON = sys.executable

UNIT_TEST_SAMPLE = """\
test_ok (tests.test_a.CaseA) ... ok
======================================================================
FAIL: test_case_is_canonicalised (tests.test_config.ConfigTest)
----------------------------------------------------------------------
已存在文件会被规范化成磁盘上的真实大小写
Traceback (most recent call last):
AssertionError: '零件1.step' != '零件1.STEP'
======================================================================
ERROR: test_other (tests.test_b.CaseB)
----------------------------------------------------------------------
Traceback (most recent call last):
RuntimeError: boom
----------------------------------------------------------------------
Ran 513 tests in 20.6s
FAILED (failures=1, errors=1)
"""

VERIFY_SAMPLE = """\
  [PASS] 前端纯函数自检  断言全部通过
  [FAIL] 项目管理 CRUD 与隔离                              HTTPError: HTTP Error 409
  [FAIL] 收敛基准                                         ConnectionResetError
  [PASS] 文档里的计数与实测一致                            513 个用例 / 148 项检查
  2 项失败：项目管理 CRUD 与隔离, 收敛基准
"""

failures = 0


def run(sample: str, mode: str = "failures") -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "log.txt"
        path.write_text(sample, encoding="utf-8")
        completed = subprocess.run(
            [PYTHON, str(TOOL), str(path), "--mode", mode, "--title", "T"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return (completed.stdout or "") + (completed.stderr or "")


def expect(label: str, condition: bool, output: str) -> None:
    global failures
    print(f"[{'抓住' if condition else '**没抓住**'}] {label}")
    if not condition:
        failures += 1
        print("           实际输出：" + output.strip().replace("\n", " | ")[:200])


# 1) unittest 形式：两条失败 + 各自说明
out = run(UNIT_TEST_SAMPLE)
expect("unittest 的 FAIL 被发成注解", out.count("::error") >= 2, out)
expect("unittest 的说明行带上了", "已存在文件会被规范化" in out, out)
expect("ERROR 也被算作失败", "test_other" in out, out)

# 2) verify 形式：`[FAIL]` 行
out = run(VERIFY_SAMPLE)
expect("verify 的 [FAIL] 被发成注解", out.count("::error") == 2, out)
expect("verify 的检查名带上了", "项目管理 CRUD 与隔离" in out, out)
expect("[PASS] 行不会被误报", "[PASS]" not in out, out)

# 3) 没有失败时给一条 warning，而不是假装有失败
out = run("  [PASS] 全过\n")
expect("没有失败时只给 warning", "::error" not in out and "::warning" in out, out)

# 4) tail 模式
out = run("第一行\n第二行\n第三行\n", mode="tail")
expect("tail 模式输出最后一行", "第三行" in out, out)

# 5) 转义：消息里的换行/百分号必须转义，否则注解会被截断
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "log.txt"
    path.write_text("  [FAIL] 含 % 与 换行 的说明\n", encoding="utf-8")
    completed = subprocess.run(
        [PYTHON, str(TOOL), str(path), "--title", "T"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
out = completed.stdout or ""
expect("百分号被转义成 %25", "%25" in out, out)

# 6) 文件不存在时安静退出 0（注解是尽力而为）
completed = subprocess.run(
    [PYTHON, str(TOOL), str(REPO / "no-such-file.log")],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
expect("文件缺失时退出码 0", completed.returncode == 0, completed.stdout or "")

print()
print(f"没抓住的数量：{failures}")
sys.exit(1 if failures else 0)
