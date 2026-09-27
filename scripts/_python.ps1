# ============================================================
#  供其他脚本 dot-source：解析一个**真正可用**的 Python 解释器
#
#  为什么不能直接写 `python`：Windows 上 `python` 常常是 Microsoft Store 的
#  「应用执行别名」，调用它不会有任何输出（也不会报错），脚本会静默失效。
#  因此优先级为：后端 venv → py -3（Windows 启动器）→ python3 → python。
#
#  用法（注意不要把子进程输出"捕获"进函数返回值，否则 exit 会拿到一个数组）：
#      . (Join-Path $PSScriptRoot '_python.ps1')
#      $py = Get-PythonCommand -Root $root
#      & $py.Exe @($py.Args + @((Join-Path $root 'tools\tasks.py'), 'test'))
#      exit $LASTEXITCODE
# ============================================================

function Get-PythonCommand {
    param([Parameter(Mandatory = $true)][string]$Root)

    $venv = Join-Path $Root 'backend\venv\Scripts\python.exe'
    if (Test-Path $venv) {
        return @{ Exe = $venv; Args = @() }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Exe = 'py'; Args = @('-3') }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @{ Exe = 'python3'; Args = @() }
    }
    Write-Host '[!] 未找到可用的 Python，请安装 Python 3.9+ 或先运行 setup。' -ForegroundColor Red
    return @{ Exe = 'python'; Args = @() }
}
