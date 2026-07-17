"""从 Web 界面启动 Playwright 登录初始化进程.

Windows 下尽量弹出新控制台 + 可见浏览器；
无法弹窗时以后台进程 + 超时自动保存兜底。
不记录密码/Cookie。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.browser.session import safe_state_meta, state_file_path
from app.core.config import BACKEND_DIR, PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_process: subprocess.Popen | None = None
_started_at: float | None = None
_last_error: str | None = None
_wait_seconds: int = 600


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int | None = None
    wait_seconds: int = 600
    login_url: str = ""
    mode: str = ""  # console | background


def _python_exe() -> Path:
    # 优先当前解释器（venv）
    return Path(sys.executable)


def is_login_process_running() -> bool:
    global _process
    with _lock:
        if _process is None:
            return False
        code = _process.poll()
        if code is None:
            return True
        _process = None
        return False


def get_launcher_status() -> dict[str, Any]:
    running = is_login_process_running()
    meta = safe_state_meta()
    elapsed = None
    if _started_at is not None and running:
        elapsed = int(time.time() - _started_at)
    return {
        "process_running": running,
        "pid": _process.pid if _process and running else None,
        "started_at": _started_at,
        "elapsed_seconds": elapsed,
        "wait_seconds": _wait_seconds,
        "last_error": _last_error,
        "state": meta,
    }


def stop_login_process() -> dict[str, Any]:
    global _process, _last_error
    with _lock:
        if _process is None or _process.poll() is not None:
            _process = None
            return {"ok": True, "message": "当前没有运行中的登录进程", "stopped": False}
        try:
            _process.terminate()
            try:
                _process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _process.kill()
            msg = "已终止登录初始化进程"
            logger.info(msg)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            return {"ok": False, "message": f"终止失败: {exc}", "stopped": False}
        finally:
            _process = None
        return {"ok": True, "message": msg, "stopped": True}


def start_login_process(
    *,
    login_url: str | None = None,
    wait_seconds: int = 600,
    force: bool = False,
) -> LaunchResult:
    """启动登录初始化.

    force=True 时先结束已有进程再启动。
    """
    global _process, _started_at, _last_error, _wait_seconds

    settings = get_settings()
    url = (login_url or settings.login_source_login_url or "https://www.baidu.com/").strip()
    wait_seconds = max(60, min(int(wait_seconds), 1800))
    _wait_seconds = wait_seconds

    if is_login_process_running():
        if not force:
            return LaunchResult(
                ok=False,
                message="登录初始化已在进行中。请在弹出的浏览器中完成登录，或先点「停止」再重试。",
                pid=_process.pid if _process else None,
                wait_seconds=wait_seconds,
                login_url=url,
                mode="running",
            )
        stop_login_process()

    py = _python_exe()
    cmd = [
        str(py),
        "-m",
        "app.tools.login_init",
        "--url",
        url,
        "--wait",
        str(wait_seconds),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    # Windows: 新控制台，便于用户按 Enter
    creationflags = 0
    mode = "background"
    popen_kwargs: dict[str, Any] = {
        "cwd": str(BACKEND_DIR),
        "env": env,
    }
    if sys.platform == "win32":
        # CREATE_NEW_CONSOLE = 0x00000010
        creationflags = 0x00000010
        popen_kwargs["creationflags"] = creationflags
        mode = "console"
        # 通过 cmd /k 更稳
        cmd = [
            "cmd.exe",
            "/c",
            "start",
            "FusionBid-Login",
            "cmd.exe",
            "/k",
            f'cd /d "{BACKEND_DIR}" && "{py}" -m app.tools.login_init --url "{url}" --wait {wait_seconds}',
        ]
        # start 会立即返回，单独记一个“监视”进程不适用；改用直接 Popen python + CREATE_NEW_CONSOLE
        cmd = [str(py), "-m", "app.tools.login_init", "--url", url, "--wait", str(wait_seconds)]
        popen_kwargs["creationflags"] = 0x00000010  # NEW_CONSOLE

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception as exc:  # noqa: BLE001
        # 回退：无新控制台后台跑（超时自动保存）
        logger.warning("console launch failed, fallback background: %s", exc)
        try:
            proc = subprocess.Popen(
                [str(py), "-m", "app.tools.login_init", "--url", url, "--wait", str(wait_seconds)],
                cwd=str(BACKEND_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            mode = "background"
        except Exception as exc2:  # noqa: BLE001
            _last_error = str(exc2)
            return LaunchResult(
                ok=False,
                message=f"启动登录失败: {exc2}",
                wait_seconds=wait_seconds,
                login_url=url,
            )

    with _lock:
        _process = proc
        _started_at = time.time()
        _last_error = None

    # 短暂等待确认进程活着
    time.sleep(0.8)
    if proc.poll() is not None:
        _last_error = f"登录进程立即退出，code={proc.returncode}"
        return LaunchResult(
            ok=False,
            message=(
                f"登录进程未能保持运行（退出码 {proc.returncode}）。"
                f"也可双击 scripts\\run_login_init.bat 启动。"
            ),
            wait_seconds=wait_seconds,
            login_url=url,
            mode=mode,
        )

    msg = (
        "已启动登录浏览器。"
        "请在弹出的浏览器中登录目标招采网站；"
        f"完成后在弹出的控制台按 Enter，或等待约 {wait_seconds} 秒自动保存。"
        if mode == "console"
        else (
            "已在后台启动登录浏览器。"
            f"请在弹出窗口登录，约 {wait_seconds} 秒后自动保存 Cookie。"
            "若无窗口，请双击 scripts\\run_login_init.bat。"
        )
    )
    return LaunchResult(
        ok=True,
        message=msg,
        pid=proc.pid,
        wait_seconds=wait_seconds,
        login_url=url,
        mode=mode,
    )


def clear_login_state() -> dict[str, Any]:
    """删除本地 storage state（需重新登录）."""
    path = state_file_path()
    if path.exists():
        path.unlink()
        return {"ok": True, "message": "已清除本地登录态文件，请重新初始化登录", "cleared": True}
    return {"ok": True, "message": "本来就没有登录态文件", "cleared": False}


def open_scripts_folder() -> dict[str, Any]:
    """打开 scripts 目录方便双击 bat（Windows）."""
    scripts = PROJECT_ROOT / "scripts"
    try:
        if sys.platform == "win32":
            os.startfile(str(scripts))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(scripts)])
        return {"ok": True, "message": f"已打开目录: {scripts}", "path": str(scripts)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "path": str(scripts)}
