# FusionBid 本地启动脚本 (Windows PowerShell)
# 用法: .\scripts\start.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> FusionBid 智标聚合助手 - 启动开发环境" -ForegroundColor Cyan

$portListener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($portListener) {
    try {
        $existingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
        if ([string]$existingHealth.app -like "*FusionBid*") {
            if (
                [string]$existingHealth.extraction_version -eq "v2" -and
                [string]$existingHealth.database_revision -eq "20260718_0006"
            ) {
                Write-Host "FusionBid 已是当前版本，直接打开页面。" -ForegroundColor Green
                Start-Process "http://127.0.0.1:8000/"
                return
            }

            $expectedPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($portListener.OwningProcess)"
            $belongsToWorkspace = $false
            if ($processInfo) {
                $belongsToWorkspace = (
                    ([string]$processInfo.ExecutablePath).StartsWith(
                        (Split-Path -Parent $expectedPython),
                        [System.StringComparison]::OrdinalIgnoreCase
                    ) -or
                    ([string]$processInfo.CommandLine).Contains($Root)
                )
            }
            if (-not $belongsToWorkspace) {
                throw "8000 端口上是另一个 FusionBid 实例（PID $($portListener.OwningProcess)），无法确认属于当前工作区，脚本不会结束它。"
            }
            $answer = Read-Host "检测到当前工作区的旧 FusionBid（PID $($portListener.OwningProcess)），是否安全重启？ [y/N]"
            if ($answer -notmatch "^[Yy]") {
                Write-Host "已取消重启。" -ForegroundColor Yellow
                return
            }
            Stop-Process -Id $portListener.OwningProcess -ErrorAction Stop
            Start-Sleep -Seconds 2
            if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
                throw "旧 FusionBid 未能安全停止；请手工关闭原窗口后重试，脚本不使用强制结束。"
            }
            Write-Host "旧 FusionBid 已停止，将迁移数据库并启动新版本。" -ForegroundColor Yellow
            $portListener = $null
        }
    } catch {
        # 端口占用但不是可识别的 FusionBid，交由下方错误处理。
    }
    if ($portListener) {
        throw "端口 8000 已被其他进程占用（PID $($portListener.OwningProcess)），脚本不会强制结束该进程。"
    }
}

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
& $venvPython -m alembic upgrade head
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
    "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"
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
