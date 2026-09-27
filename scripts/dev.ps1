# ============================================================
#  SimCloud AI - 一键启动前后端（Windows 薄封装）
#  真正的实现在 tools/tasks.py —— 跨平台唯一实现。
#  用法: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
# ============================================================
. (Join-Path $PSScriptRoot '_python.ps1')
$root = Split-Path -Parent $PSScriptRoot
$py = Get-PythonCommand -Root $root
& $py.Exe @($py.Args + @((Join-Path $root 'tools\tasks.py'), 'dev'))
exit $LASTEXITCODE
