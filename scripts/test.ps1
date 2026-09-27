# ============================================================
#  SimCloud AI - 运行后端回归测试
#  用法: powershell -ExecutionPolicy Bypass -File scripts\test.ps1
#  特点: 不需要启动服务器（直接调用端点函数），适合本地与 CI
# ============================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$python = Join-Path $backend 'venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host '[X] 未找到 backend\venv，请先运行 scripts\setup.ps1' -ForegroundColor Red
    exit 1
}

Write-Host '=== 运行后端测试 (unittest) ===' -ForegroundColor Cyan

# -t backend 会把 backend/ 放进 sys.path，使 `import config` 等生效
Push-Location $backend
try {
    & $python -m unittest discover -s tests -t . -v
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($code -eq 0) {
    Write-Host "`n全部测试通过 ✅" -ForegroundColor Green
} else {
    Write-Host "`n测试失败 ❌ (exit=$code)" -ForegroundColor Red
}
exit $code
