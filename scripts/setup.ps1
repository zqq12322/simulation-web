# ============================================================
#  SimCloud AI - 首次环境安装
#  用法: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# ============================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

Write-Host '=== SimCloud AI 环境安装 ===' -ForegroundColor Cyan

# --- 0. 检查工具链 -------------------------------------------------
foreach ($cmd in @('python', 'node', 'npm')) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host "[X] 找不到 $cmd，请先安装。" -ForegroundColor Red
        exit 1
    }
    Write-Host ("[ok] {0,-7} {1}" -f $cmd, (& $cmd --version 2>&1 | Select-Object -First 1))
}

# --- 1. 后端虚拟环境 -----------------------------------------------
$venvPython = Join-Path $backend 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "`n[1/4] 创建后端虚拟环境 backend\venv ..." -ForegroundColor Yellow
    & python -m venv (Join-Path $backend 'venv')
} else {
    Write-Host "`n[1/4] 后端虚拟环境已存在，跳过。" -ForegroundColor Green
}

Write-Host '[2/4] 安装后端依赖 (gmsh / scikit-fem 等，首次约 5-10 分钟)...' -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $backend 'requirements.txt')

# --- 2. 后端 .env ---------------------------------------------------
$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    Write-Host '[3/4] 生成 backend\.env（请填入 DeepSeek Key）' -ForegroundColor Yellow
    Copy-Item (Join-Path $backend '.env.example') $envFile
    Write-Host '      -> 已创建，AI 助手需要 DEEPSEEK_API_KEY 才能工作。' -ForegroundColor Gray
} else {
    Write-Host '[3/4] backend\.env 已存在，跳过。' -ForegroundColor Green
}

# --- 3. 前端依赖 ----------------------------------------------------
Write-Host '`n[4/4] 安装前端依赖 (npm install)...' -ForegroundColor Yellow
Push-Location $frontend
try {
    & npm install
    $feEnvLocal = Join-Path $frontend '.env.local'
    if (-not (Test-Path $feEnvLocal)) {
        Copy-Item (Join-Path $frontend '.env.example') $feEnvLocal
        Write-Host '      -> 已创建前端 .env.local' -ForegroundColor Gray
    }
} finally {
    Pop-Location
}

Write-Host "`n=== 安装完成 ===" -ForegroundColor Cyan
Write-Host '下一步: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1' -ForegroundColor White
