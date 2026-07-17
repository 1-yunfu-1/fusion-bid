@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title FusionBid

cd /d "%~dp0.."
set "ROOT=%CD%"

echo ========================================
echo   FusionBid - One Click Start
echo ========================================
echo.

REM ---- 1) Find a working system Python (prefer py launcher) ----
set "SYS_PY="
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%I"
)
if not defined SYS_PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "SYS_PY=%%I"
  )
)
if not defined SYS_PY (
  echo [ERROR] 未找到可用的 Python 3。
  echo 请安装 Python 3.12+：https://www.python.org/downloads/
  echo 安装时务必勾选 "Add python.exe to PATH"
  echo 不要使用已损坏的 Windows Store 占位 python。
  pause
  exit /b 1
)

echo [OK] System Python: %SYS_PY%
"%SYS_PY%" -c "import sys; v=sys.version_info; assert v.major==3 and v.minor>=12, f'need Python>=3.12, got {sys.version}'" 2>nul
if errorlevel 1 (
  echo [ERROR] 需要 Python 3.12 或更高版本。
  "%SYS_PY%" -c "import sys; print(sys.version)"
  pause
  exit /b 1
)

if not exist "%ROOT%\.env" (
  if exist "%ROOT%\.env.example" (
    copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [OK] Created .env from .env.example ^(no API key^)
  )
)

set "VENV_DIR=%ROOT%\backend\.venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "NEED_VENV=0"

if not exist "%PY%" (
  set "NEED_VENV=1"
) else (
  REM venv exists but base interpreter may be missing ^(copied from another PC^)
  "%PY%" -c "import sys; print(sys.version)" >nul 2>&1
  if errorlevel 1 (
    echo [WARN] 现有 .venv 已损坏或来自其他电脑，将删除并重建...
    set "NEED_VENV=1"
  ) else (
    REM also fail if pyvenv.cfg points to non-existent home
    if exist "%VENV_DIR%\pyvenv.cfg" (
      findstr /I /C:"Users\\user\\AppData" "%VENV_DIR%\pyvenv.cfg" >nul 2>&1
      if not errorlevel 1 (
        echo [WARN] 检测到打包误带的开发机虚拟环境，将重建...
        set "NEED_VENV=1"
      )
    )
  )
)

if "!NEED_VENV!"=="1" (
  if exist "%VENV_DIR%" (
    echo [1/4] Removing broken venv...
    rmdir /s /q "%VENV_DIR%" 2>nul
  )
  echo [1/4] Creating new venv with local Python...
  pushd "%ROOT%\backend"
  "%SYS_PY%" -m venv .venv
  if errorlevel 1 (
    echo [ERROR] 创建虚拟环境失败。
    echo 请确认 Python 安装完整，并重试。
    popd
    pause
    exit /b 1
  )
  popd
  if not exist "%PY%" (
    echo [ERROR] venv 创建后仍找不到 python.exe
    pause
    exit /b 1
  )
) else (
  echo [1/4] venv OK
)

echo [2/4] Installing backend deps ^(first run may take several minutes^)...
"%PY%" -m pip install -q -U pip
if errorlevel 1 (
  echo [ERROR] pip 自检失败，尝试重建 venv...
  rmdir /s /q "%VENV_DIR%" 2>nul
  pushd "%ROOT%\backend"
  "%SYS_PY%" -m venv .venv
  popd
  "%PY%" -m pip install -q -U pip
  if errorlevel 1 (
    echo [ERROR] pip install failed
    echo 常见原因：网络问题 / Python 损坏 / 权限不足
    echo 也可手动执行：
    echo   "%SYS_PY%" -m venv backend\.venv
    echo   backend\.venv\Scripts\python.exe -m pip install -e backend[full]
    pause
    exit /b 1
  )
)

"%PY%" -m pip install -q -e "%ROOT%\backend[full]"
if errorlevel 1 (
  echo [ERROR] 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo [3/4] Playwright chromium ^(optional for login crawl^)...
"%PY%" -m playwright install chromium >nul 2>&1

if not exist "%ROOT%\frontend\dist\index.html" (
  echo [WARN] frontend\dist missing. UI may be API-only.
)

echo [4/4] Starting server...
echo.
echo   Open:  http://127.0.0.1:8000
echo   Docs:  http://127.0.0.1:8000/docs
echo   API Key: fill in Settings page ^(not bundled^)
echo.
echo   Close this window to stop.
echo ========================================
echo.

cd /d "%ROOT%\backend"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Stopped.
pause
endlocal
