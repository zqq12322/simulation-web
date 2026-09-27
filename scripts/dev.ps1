# ============================================================
#  SimCloud AI - 一键启动前后端
#  用法: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#  前端 http://localhost:3000   后端 http://127.0.0.1:8000
# ============================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

$venvPython = Join-Path $backend 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '[X] 未找到 backend\venv，请先运行 scripts\setup.ps1' -ForegroundColor Red
    exit 1
}

function Test-PortBusy([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# --- 后端 -----------------------------------------------------------
if (Test-PortBusy 8000) {
    Write-Host '[!] 端口 8000 已被占用，跳过后端启动。' -ForegroundColor Yellow
} else {
    Write-Host '启动后端 (uvicorn :8000)...' -ForegroundColor Cyan
    # 注意: 必须在 backend 目录下运行，uploads/ 是相对路径
    Start-Process -FilePath $venvPython `
        -ArgumentList '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $backend `
        -WindowStyle Minimized
}

# --- 前端 -----------------------------------------------------------
if (Test-PortBusy 3000) {
    Write-Host '[!] 端口 3000 已被占用，跳过前端启动。' -ForegroundColor Yellow
} else {
    Write-Host '启动前端 (vite :3000)...' -ForegroundColor Cyan
    Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', 'npm run dev' `
        -WorkingDirectory $frontend `
        -WindowStyle Minimized
}

# --- 健康检查 -------------------------------------------------------
Write-Host "`n等待服务就绪..." -ForegroundColor Gray
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 1000
    try {
        $r = Invoke-RestMethod 'http://127.0.0.1:8000/' -TimeoutSec 2
        if ($r.message) { $ok = $true; break }
    } catch { }
}
if ($ok) { Write-Host '[ok] 后端已就绪' -ForegroundColor Green }
else     { Write-Host '[X] 后端未在 30 秒内就绪，请查看弹出的窗口中的报错。' -ForegroundColor Red }

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host '  前端  : http://localhost:3000' -ForegroundColor White
Write-Host '  后端  : http://127.0.0.1:8000' -ForegroundColor White
Write-Host '  API文档: http://127.0.0.1:8000/docs' -ForegroundColor White
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '关闭服务: 直接关掉最小化的那两个窗口即可。' -ForegroundColor Gray
