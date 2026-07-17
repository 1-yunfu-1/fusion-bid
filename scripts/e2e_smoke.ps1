# 离线端到端冒烟
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "请先创建 backend\.venv 并安装依赖" -ForegroundColor Red
    exit 1
}
& $py (Join-Path $Root "scripts\e2e_smoke.py")
exit $LASTEXITCODE
