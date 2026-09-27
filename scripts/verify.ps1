# ============================================================
#  SimCloud AI - 端到端验证（Windows 薄封装）
#  真正的实现在 tools/tasks.py —— 跨平台唯一实现。
#  用法: powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
#  前提: 后端已在 8000 端口运行
# ============================================================
. (Join-Path $PSScriptRoot '_python.ps1')
$root = Split-Path -Parent $PSScriptRoot
$py = Get-PythonCommand -Root $root
& $py.Exe @($py.Args + @((Join-Path $root 'tools\tasks.py'), 'verify'))
exit $LASTEXITCODE
