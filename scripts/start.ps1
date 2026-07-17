# FusionBid 本地启动脚本 (Windows PowerShell)
# 用法: .\scripts\start.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> FusionBid 智标聚合助手 - 启动开发环境" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已从 .env.example 创建 .env" -ForegroundColor Yellow
}

# 后端虚拟环境
$venvPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "创建 Python 虚拟环境..." -ForegroundColor Cyan
    Push-Location backend
    python -m venv .venv
    Pop-Location
}

Write-Host "安装后端依赖..." -ForegroundColor Cyan
Push-Location backend
& $venvPython -m pip install -q -U pip
& $venvPython -m pip install -q -e ".[dev]"
Pop-Location


Write-Host "安装前端依赖..." -ForegroundColor Cyan
Push-Location frontend
if (-not (Test-Path "node_modules")) {
    npm install
}
Pop-Location

# 启动后端
Write-Host "启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Green
$backend = Start-Process -PassThru -NoNewWindow -FilePath $venvPython -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"
) -WorkingDirectory (Join-Path $Root "backend")

Start-Sleep -Seconds 2

Write-Host "启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Green
Push-Location frontend
try {
    npm run dev
} finally {
    if (-not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
