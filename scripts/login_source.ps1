# FusionBid 登录态数据源初始化（Windows PowerShell）
# 用法: 右键“使用 PowerShell 运行”，或双击 scripts\run_login_init.bat
# 合规：可见浏览器 + 用户手动登录 + 本地 storage state，不保存明文密码

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> FusionBid 登录态初始化" -ForegroundColor Cyan
Write-Host "将打开可见浏览器，请手动登录目标招采门户。" -ForegroundColor Yellow
Write-Host "验证码请自行完成；storage state 写入 data/browser_states/。" -ForegroundColor Yellow

$venvPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "未找到 backend\.venv，正在创建..." -ForegroundColor Yellow
    Push-Location backend
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -U pip
    .\.venv\Scripts\python.exe -m pip install -q -e ".[dev,full]"
    Pop-Location
}

Write-Host "检查 Playwright..." -ForegroundColor Cyan
& $venvPython -c "import playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 playwright..." -ForegroundColor Yellow
    & $venvPython -m pip install -q playwright
    & $venvPython -m playwright install chromium
}

Push-Location (Join-Path $Root "backend")
& $venvPython -m app.tools.login_init --wait 600 @args
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) {
    Write-Host "登录初始化失败，退出码 $code" -ForegroundColor Red
    Read-Host "按 Enter 关闭"
}
exit $code
