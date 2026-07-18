"""FusionBid 管理的公开站点专用 Chrome/Edge。

浏览器使用独立用户目录和仅回环可访问的 CDP 端口。Playwright 只连接
已经由系统启动的普通浏览器，不注入登录态、不读取日常浏览器配置，也不
添加隐藏自动化特征的启动参数。
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import re
import socket
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

PUBLIC_BROWSER_STATES = {
    "not_started",
    "starting",
    "ready",
    "busy",
    "needs_verification",
    "unavailable",
}


class ManagedPublicBrowserError(RuntimeError):
    """专用公开站点浏览器不可用。"""


@dataclass(frozen=True)
class ManagedBrowserLease:
    context: Any
    reused: bool
    engine: str


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _public_error_message(value: object) -> str:
    message = str(value or "专用浏览器不可用")[:1000]
    message = re.sub(r"(?i)\b[a-z]:[\\/]\S+", "[local path]", message)
    message = re.sub(r"(?:127\.0\.0\.1|localhost):\d+", "loopback", message)
    return message[:500]


def _browser_candidates() -> list[tuple[str, Path]]:
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    )
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        ("chrome", program_files / "Google/Chrome/Application/chrome.exe"),
        ("chrome", program_files_x86 / "Google/Chrome/Application/chrome.exe"),
        ("chrome", local_app_data / "Google/Chrome/Application/chrome.exe"),
        ("msedge", program_files_x86 / "Microsoft/Edge/Application/msedge.exe"),
        ("msedge", program_files / "Microsoft/Edge/Application/msedge.exe"),
    ]
    output: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for engine, path in candidates:
        key = str(path).lower()
        if path.is_file() and key not in seen:
            seen.add(key)
            output.append((engine, path.resolve()))
    return output


def _process_executable(pid: int) -> Path | None:
    """只读校验运行时标记中的 PID，避免操作未知浏览器进程。"""
    if os.name != "nt" or pid <= 0:
        return None
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        )
        return Path(buffer.value).resolve() if ok else None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class ManagedPublicBrowser:
    """延迟启动、串行复用并安全关闭专用 Chrome/Edge。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.profile_dir = settings.browser_states_dir / "cebpub_managed_profile"
        self.runtime_file = settings.browser_states_dir / "cebpub_managed_runtime.json"
        self._state = "not_started"
        self._engine: str | None = None
        self._last_error: str | None = None
        self._pid: int | None = None
        self._port: int | None = None
        self._process: subprocess.Popen | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._work_page: Any = None
        self._operation_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        if not self._browser or not self._browser.is_connected():
            return False
        if self._process is not None and self._process.poll() is not None:
            return False
        if (
            os.name == "nt"
            and self._pid
            and self._process is None
            and _process_executable(self._pid) is None
        ):
            return False
        return True

    def status(self) -> dict[str, Any]:
        state = self._state if self._state in PUBLIC_BROWSER_STATES else "unavailable"
        last_error = self._last_error
        if state in {"ready", "busy", "needs_verification"} and not self.is_connected:
            state = "unavailable"
            last_error = last_error or "专用浏览器进程已退出，将在下次采集时自动重启"
        try:
            profile_ready = self.profile_dir.is_dir() and any(
                self.profile_dir.iterdir()
            )
        except OSError:
            profile_ready = False
        return {
            "state": state,
            "engine": self._engine,
            "profile_ready": profile_ready,
            "last_error": last_error,
        }

    async def _cdp_ready(self, port: int, timeout_seconds: float = 20) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with httpx.AsyncClient(trust_env=False, timeout=1.5) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(
                        f"http://127.0.0.1:{port}/json/version"
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        return bool(payload.get("webSocketDebuggerUrl"))
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.25)
        return False

    async def _connect(self, port: int) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ManagedPublicBrowserError(
                "Playwright 未安装，请使用一键启动脚本安装完整依赖"
            ) from exc
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                is_local=True,
                no_defaults=True,
                timeout=20_000,
            )
            if not browser.contexts:
                raise ManagedPublicBrowserError("专用浏览器没有可用默认上下文")
        except Exception:
            await playwright.stop()
            raise
        self._playwright = playwright
        self._browser = browser
        self._work_page = None

    def _read_runtime(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.runtime_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def _write_runtime(self, *, executable: Path) -> None:
        payload = {
            "pid": self._pid,
            "port": self._port,
            "engine": self._engine,
            "executable": str(executable),
            "profile_dir": str(self.profile_dir.resolve()),
            "started_at": datetime.now(TZ).isoformat(),
        }
        self.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def _try_attach_previous(self) -> bool:
        payload = self._read_runtime()
        if not payload:
            return False
        try:
            pid = int(payload["pid"])
            port = int(payload["port"])
            executable = Path(str(payload["executable"])).resolve()
            profile = Path(str(payload["profile_dir"])).resolve()
            engine = str(payload["engine"])
        except (KeyError, TypeError, ValueError, OSError):
            self.runtime_file.unlink(missing_ok=True)
            return False
        known = {path for _, path in _browser_candidates()}
        running_executable = _process_executable(pid)
        if (
            executable not in known
            or profile != self.profile_dir.resolve()
            or running_executable != executable
            or not await self._cdp_ready(port, timeout_seconds=2)
        ):
            self.runtime_file.unlink(missing_ok=True)
            return False
        await self._connect(port)
        self._pid = pid
        self._port = port
        self._engine = engine
        logger.info("reused managed public browser engine=%s pid=%s", engine, pid)
        return True

    async def _stop_exact_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)

    async def _start_new(self) -> None:
        candidates = _browser_candidates()
        if not candidates:
            raise ManagedPublicBrowserError("未找到本机 Chrome 或 Edge")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        failures: list[str] = []
        for engine, executable in candidates:
            port = _free_loopback_port()
            args = [
                str(executable),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self.profile_dir.resolve()}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-minimized",
                "about:blank",
            ]
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            if not await self._cdp_ready(port):
                failures.append(f"{engine}: CDP 启动超时")
                await self._stop_exact_process(process)
                continue
            try:
                await self._connect(port)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{engine}: {type(exc).__name__}")
                await self._stop_exact_process(process)
                continue
            self._process = process
            self._pid = process.pid
            self._port = port
            self._engine = engine
            self._write_runtime(executable=executable)
            logger.info("started managed public browser engine=%s pid=%s", engine, process.pid)
            return
        raise ManagedPublicBrowserError("；".join(failures) or "专用浏览器启动失败")

    async def ensure_started(self) -> bool:
        """确保浏览器可用；返回本次调用前是否已经存在可复用会话。"""
        if self.is_connected:
            return True
        async with self._lifecycle_lock:
            if self.is_connected:
                return True
            if self._browser is not None or self._playwright is not None:
                await self._disconnect_playwright()
            if self._process is not None and self._process.poll() is not None:
                self._process = None
            self._state = "starting"
            self._last_error = None
            try:
                reused = await self._try_attach_previous()
                if not reused:
                    if self._process is not None:
                        await self._stop_exact_process(self._process)
                        self._process = None
                    await self._start_new()
                self._state = "ready"
                return reused
            except Exception as exc:  # noqa: BLE001
                self._state = "unavailable"
                self._last_error = _public_error_message(exc)
                await self._disconnect_playwright()
                raise ManagedPublicBrowserError(self._last_error) from exc

    async def get_work_page(self, context):
        """复用固定工作页，避免每次采集都闪现并关闭一个浏览器窗口。"""
        page = self._work_page
        if page is not None:
            try:
                if not page.is_closed():
                    return page
            except Exception:  # noqa: BLE001
                pass
        available = []
        try:
            available = [
                candidate
                for candidate in context.pages
                if not candidate.is_closed()
            ]
        except Exception:  # noqa: BLE001
            available = []
        self._work_page = available[0] if available else await context.new_page()
        return self._work_page

    async def _bring_to_front(self) -> None:
        if os.name != "nt" or not self._pid:
            return

        def show() -> None:
            user32 = ctypes.windll.user32
            target_pid = int(self._pid or 0)
            found: list[int] = []
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def enum_window(hwnd, _lparam):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == target_pid and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(callback_type(enum_window), 0)
            if found:
                user32.ShowWindow(found[0], 9)  # SW_RESTORE
                user32.SetForegroundWindow(found[0])

        await asyncio.to_thread(show)

    @asynccontextmanager
    async def acquire(self, *, interactive: bool = False) -> AsyncIterator[ManagedBrowserLease]:
        """串行租用默认上下文，避免并发标签触发站点风控。"""
        async with self._operation_lock:
            reused = await self.ensure_started()
            if interactive:
                await self._bring_to_front()
            self._state = "busy"
            try:
                yield ManagedBrowserLease(
                    context=self._browser.contexts[0],
                    reused=reused,
                    engine=self._engine or "chromium",
                )
            finally:
                if self._state == "busy":
                    self._state = "ready" if self.is_connected else "unavailable"

    def mark_needs_verification(self) -> None:
        self._state = "needs_verification"

    async def _disconnect_playwright(self, *, close_browser: bool = False) -> None:
        browser = self._browser
        playwright = self._playwright
        self._work_page = None
        self._browser = None
        self._playwright = None
        if close_browser and browser:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:  # noqa: BLE001
                pass

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            await self._disconnect_playwright(close_browser=True)
            process = self._process
            self._process = None
            if process:
                await self._stop_exact_process(process)
            self.runtime_file.unlink(missing_ok=True)
            self._pid = None
            self._port = None
            self._engine = None
            self._state = "not_started"
            self._last_error = None


_managed_public_browser: ManagedPublicBrowser | None = None


def get_managed_public_browser() -> ManagedPublicBrowser:
    global _managed_public_browser
    if _managed_public_browser is None:
        _managed_public_browser = ManagedPublicBrowser()
    return _managed_public_browser


def managed_public_browser_status() -> dict[str, Any]:
    return get_managed_public_browser().status()


async def shutdown_managed_public_browser() -> None:
    global _managed_public_browser
    if _managed_public_browser is not None:
        await _managed_public_browser.shutdown()
        _managed_public_browser = None
