# FusionBid release packager (Windows)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\package_release.ps1
# Output: dist_release\FusionBid-release-YYYYMMDD-HHMM\ and .zip
# Excludes: API keys, .env secrets, llm_secrets, login cookies, local DB

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$pkgName = "FusionBid-release-$stamp"
$outRoot = Join-Path $Root "dist_release"
$stage = Join-Path $outRoot $pkgName

Write-Host "==> Package dir: $stage" -ForegroundColor Cyan

if (Test-Path $stage) {
    Remove-Item -Recurse -Force $stage
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
New-Item -ItemType Directory -Path $outRoot -Force | Out-Null

Write-Host "==> Building frontend..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "frontend")
if (-not (Test-Path "node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}
$env:VITE_API_BASE_URL = ""
npm run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
Pop-Location

function Copy-TreeFiltered {
    param(
        [string]$Source,
        [string]$Dest,
        [string[]]$ExcludeDirNames
    )
    if (-not (Test-Path $Dest)) {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    }
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        $name = $_.Name
        if ($_.PSIsContainer) {
            if ($ExcludeDirNames -contains $name) { return }
            $childDest = Join-Path $Dest $name
            Copy-TreeFiltered -Source $_.FullName -Dest $childDest -ExcludeDirNames $ExcludeDirNames
        }
        else {
            if ($name -eq ".env") { return }
            if ($name -eq "llm_secrets.json") { return }
            if ($name -like "*_state.json") { return }
            if ($name -like "storage_state*") { return }
            if ($name -like "*.db") { return }
            if ($name -like "*.sqlite*") { return }
            if ($name -eq "desktop.ini") { return }
            if ($name -eq "Thumbs.db") { return }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dest $name) -Force
        }
    }
}

$excludeDirs = @(
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".idea",
    ".vscode",
    "dist_release",
    "htmlcov",
    "fusion_bid_backend.egg-info",
    ".eggs"
)

Write-Host "==> Copying project files..." -ForegroundColor Cyan
Copy-TreeFiltered -Source $Root -Dest $stage -ExcludeDirNames $excludeDirs

$srcDist = Join-Path $Root "frontend\dist"
$dstDist = Join-Path $stage "frontend\dist"
if (Test-Path $srcDist) {
    if (Test-Path $dstDist) {
        Remove-Item -Recurse -Force $dstDist
    }
    Copy-Item -Recurse -Force $srcDist $dstDist
}

@("data", "data\reports", "data\browser_states", "data\fixtures") | ForEach-Object {
    $p = Join-Path $stage $_
    New-Item -ItemType Directory -Force -Path $p | Out-Null
    $keep = Join-Path $p ".gitkeep"
    if (-not (Test-Path $keep)) {
        New-Item -ItemType File -Path $keep -Force | Out-Null
    }
}

$envExample = Join-Path $Root ".env.example"
if (Test-Path $envExample) {
    Copy-Item $envExample (Join-Path $stage ".env.example") -Force
}

# Launchers
$startAll = Join-Path $stage "scripts\start_all.bat"
if (-not (Test-Path $startAll)) {
    throw "missing scripts\start_all.bat in package"
}

$rootBat = Join-Path $stage "start.bat"
Set-Content -Path $rootBat -Encoding ASCII -Value @(
    "@echo off",
    "cd /d `"%~dp0`"",
    "call `"%~dp0scripts\start_all.bat`""
)

# UTF-8 Chinese filename: 一键启动.bat
$yiJianName = [System.IO.Path]::Combine($stage, [System.Text.Encoding]::UTF8.GetString([byte[]](0xE4,0xB8,0x80,0xE9,0x94,0xAE,0xE5,0x90,0xAF,0xE5,0x8A,0xA8)) + ".bat")
Set-Content -Path $yiJianName -Encoding ASCII -Value @(
    "@echo off",
    "cd /d `"%~dp0`"",
    "call `"%~dp0scripts\start_all.bat`""
)

# User-facing Chinese README (replace developer README.md)
$releaseReadme = Join-Path $Root "scripts\release_README.md"
if (Test-Path $releaseReadme) {
    Copy-Item -Force $releaseReadme (Join-Path $stage "README.md")
    Copy-Item -Force $releaseReadme (Join-Path $stage "使用说明.md")
} else {
    Write-Host "WARN: scripts\release_README.md missing" -ForegroundColor Yellow
}

$noKeySrc = Join-Path $Root "scripts\release_NO_API_KEY.txt"
if (Test-Path $noKeySrc) {
    Copy-Item -Force $noKeySrc (Join-Path $stage "NO_API_KEY.txt")
}

# Short English pointer for bilingual users
$enQuick = @"
FusionBid - Quick Start (see README.md for full Chinese guide)

1. Double-click start.bat
2. Open http://127.0.0.1:8000/  (must show web UI, not raw JSON)
3. API Key is NOT included - configure in Settings page

Stop: close the console window.
"@
Set-Content -Path (Join-Path $stage "README_START.txt") -Value $enQuick -Encoding UTF8

Get-ChildItem -Path $stage -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object {
    $n = $_.Name
    $remove = $false
    if ($n -eq ".env") { $remove = $true }
    if ($n -eq "llm_secrets.json") { $remove = $true }
    if ($n -like "*_state.json") { $remove = $true }
    if ($n -like "*.db") { $remove = $true }
    if ($n -like "*.sqlite*") { $remove = $true }
    if ($remove) {
        Write-Host "  remove: $($_.FullName)" -ForegroundColor Yellow
        Remove-Item -Force -LiteralPath $_.FullName
    }
}

$zipPath = Join-Path $outRoot "$pkgName.zip"
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Write-Host "==> Zipping $zipPath ..." -ForegroundColor Cyan
Compress-Archive -Path $stage -DestinationPath $zipPath -CompressionLevel Optimal

$sizeDir = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 2)
$sizeZip = [math]::Round(((Get-Item $zipPath).Length / 1MB), 2)
Write-Host ""
Write-Host "DONE" -ForegroundColor Green
Write-Host "  Folder: $stage ($sizeDir MB)"
Write-Host "  Zip:    $zipPath ($sizeZip MB)"
Write-Host "  Excluded: API keys, .env, llm_secrets, browser states, local DB"
Write-Host "  User: unzip and double-click start.bat"
