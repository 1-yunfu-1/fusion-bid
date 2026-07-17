"""Playwright 登录态会话：可见浏览器手动登录 + storage state 持久化.

合规约束：
- 不自动填密码、不绕过验证码、不破解付费
- storage state 仅存本地，禁止提交 Git
- 日志不输出 Cookie/Token
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


class LoginRequiredError(RuntimeError):
    """登录态失效或未登录."""


class BrowserNotAvailableError(RuntimeError):
    """Playwright 未安装或浏览器不可用."""


@dataclass
class SessionStatus:
    state_file: Path
    exists: bool
    size: int = 0
    modified_at: str | None = None
    login_ok: bool | None = None
    message: str = ""


def state_file_path(filename: str | None = None) -> Path:
    settings = get_settings()
    name = filename or settings.login_source_state_file
    path = Path(name)
    if not path.is_absolute():
        path = settings.browser_states_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def describe_state_file(path: Path | None = None) -> SessionStatus:
    p = path or state_file_path()
    if not p.exists():
        return SessionStatus(
            state_file=p,
            exists=False,
            message="尚未保存登录状态，请到「数据源」页启动登录浏览器",
        )
    stat = p.stat()
    return SessionStatus(
        state_file=p,
        exists=True,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=TZ).isoformat(),
        message="已存在 storage state 文件（内容不在接口中展示）",
    )


def _ensure_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        raise BrowserNotAvailableError(
            "未安装 Playwright。请执行: pip install playwright && playwright install chromium"
        ) from exc


def _launch_browser(p, *, headless: bool = False):
    """优先本机 Chrome/Edge（指纹更像真人，降低 WAF 拦截），否则用 Playwright Chromium."""
    last_err: Exception | None = None
    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return p.chromium.launch(
                    channel=channel,
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            return p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("launch channel=%s failed: %s", channel, exc)
    raise BrowserNotAvailableError(f"无法启动浏览器: {last_err}")


def interactive_login(
    *,
    login_url: str,
    state_path: Path | None = None,
    headless: bool = False,
    wait_seconds: int = 300,
) -> Path:
    """打开可见浏览器供用户手动登录，回车后保存 storage state."""
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    path = state_path or state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FusionBid 登录态初始化（合规模式）")
    print("- 将打开可见浏览器（优先本机 Chrome/Edge）")
    print("- 若目标站拦截，请在地址栏改开你能登录的招采门户")
    print("- 登录成功后回到本终端按 Enter 保存状态")
    print("- 不会记录您的账号密码到日志或代码")
    print(f"- 目标 URL: {login_url}")
    print(f"- 状态文件: {path}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 打开 {login_url} 失败或被拦截: {exc}")
            print("请在浏览器地址栏手动输入你能访问并登录的招采网站地址。")
            try:
                page.goto("about:blank")
            except Exception:  # noqa: BLE001
                pass

        import sys
        import threading

        wait_sec = max(int(wait_seconds), 60)
        print(
            f"请在浏览器中完成登录（被拦截可手动改地址）。\n"
            f"- 交互终端：登录后按 Enter 立即保存\n"
            f"- 非交互环境：约 {wait_sec} 秒后自动保存（请抓紧登录）"
        )
        done = threading.Event()

        def _wait_enter() -> None:
            try:
                if not sys.stdin.isatty():
                    # 无交互 stdin：不立刻结束，交给超时
                    return
                input("登录完成后，请按 Enter 保存登录状态…")
                done.set()
            except EOFError:
                return

        t = threading.Thread(target=_wait_enter, daemon=True)
        t.start()
        finished = done.wait(timeout=wait_sec)
        if finished:
            print("\n收到确认，正在保存登录状态…")
        else:
            print(f"\n已等待 {wait_sec} 秒，自动保存当前浏览器登录状态…")
        context.storage_state(path=str(path))
        browser.close()

    # 不读取/打印 state 内容
    logger.info("storage state saved to %s (content not logged)", path.name)
    return path


async def fetch_page_with_state(
    url: str,
    *,
    state_path: Path | None = None,
    timeout_ms: int = 60_000,
    wait_until: str = "domcontentloaded",
) -> str:
    """使用已保存登录态访问页面，返回 HTML 文本."""
    _ensure_playwright()
    from playwright.async_api import async_playwright

    path = state_path or state_file_path()
    if not path.exists():
        raise LoginRequiredError(
            "登录状态文件不存在，请到「数据源」页启动登录浏览器完成手动登录"
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                storage_state=str(path),
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            html = await page.content()
            await context.close()
        finally:
            await browser.close()
    return html


def looks_like_login_wall(html: str, markers: list[str] | None = None) -> bool:
    settings = get_settings()
    marks = markers or [
        m.strip()
        for m in settings.login_source_login_markers.split(",")
        if m.strip()
    ]
    text = html or ""
    # 登录墙常见文案
    hits = sum(1 for m in marks if m and m in text)
    return hits >= 1 and ("登录" in text[:5000] or "login" in text.lower()[:5000] or hits >= 2)


def looks_like_logged_in(html: str, markers: list[str] | None = None) -> bool:
    settings = get_settings()
    marks = markers or [
        m.strip()
        for m in settings.login_source_logged_in_markers.split(",")
        if m.strip()
    ]
    return any(m and m in (html or "") for m in marks)


def safe_state_meta(path: Path | None = None) -> dict[str, Any]:
    """返回不含 Cookie 的元信息."""
    st = describe_state_file(path)
    rel = str(st.state_file)
    try:
        rel = str(st.state_file.relative_to(PROJECT_ROOT))
    except ValueError:
        rel = st.state_file.name
    return {
        "exists": st.exists,
        "path": rel,
        "size": st.size,
        "modified_at": st.modified_at,
        "message": st.message,
    }


def validate_state_file_not_logged(path: Path | None = None) -> None:
    """开发辅助：确保不在日志中 dump state."""
    p = path or state_file_path()
    if p.exists():
        # 仅检查是合法 JSON 结构，不打印内容
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            # cookies 字段存在但不输出
            _ = "cookies" in data
        except Exception as exc:  # noqa: BLE001
            raise LoginRequiredError(f"storage state 文件损坏，请重新登录: {exc}") from exc
