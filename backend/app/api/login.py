"""登录态：状态查询 + 从界面启动 Playwright 登录初始化."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.browser.session import safe_state_meta, state_file_path
from app.core.config import get_settings
from app.services.login_launcher import (
    clear_login_state,
    get_launcher_status,
    open_scripts_folder,
    start_login_process,
    stop_login_process,
)
from app.sources.registry import get_source

router = APIRouter(prefix="/login", tags=["login"])


class LoginStartRequest(BaseModel):
    login_url: str | None = Field(
        default=None,
        description="登录页 URL；默认用配置 LOGIN_SOURCE_LOGIN_URL",
    )
    wait_seconds: int = Field(default=600, ge=60, le=1800, description="自动保存等待秒数")
    force: bool = Field(default=False, description="已有进程时是否强制重启")


@router.get("/status")
async def login_status() -> dict:
    settings = get_settings()
    meta = safe_state_meta()
    launcher = get_launcher_status()
    source = get_source("login_portal")
    health = None
    # 初始化进行中时避免长时间卡在健康检查
    if source and not launcher.get("process_running"):
        try:
            h = await source.health_check()
            health = {
                "ok": h.ok,
                "message": h.message,
                "login_ok": h.login_ok,
                "requires_login": h.requires_login,
            }
        except Exception as exc:  # noqa: BLE001
            health = {"ok": False, "message": str(exc), "login_ok": False}
    elif launcher.get("process_running"):
        health = {
            "ok": False,
            "message": "登录初始化进行中，请在弹出的浏览器中完成登录",
            "login_ok": False,
            "requires_login": True,
        }

    return {
        "enabled": bool(source and source.enabled),
        "home_url": settings.login_source_home_url,
        "login_url": settings.login_source_login_url,
        "search_url_template": settings.login_source_search_url,
        "state": meta,
        "health": health,
        "launcher": launcher,
        "instructions": [
            "推荐：在本页点击「启动登录浏览器」，在弹出窗口中登录后等待自动保存。",
            "普通 Chrome/Edge 里登录网站不会同步到本系统。",
            "若网页按钮无法弹窗：双击 scripts\\run_login_init.bat。",
            "登录成功后状态文件: data/browser_states/login_portal_state.json",
            "切勿将账号密码写入代码或提交 Git；公开源不依赖登录态。",
        ],
        "cli": "python -m app.tools.login_init",
        "state_file": str(state_file_path().name),
    }


@router.post("/start")
async def login_start(body: LoginStartRequest | None = None) -> dict:
    """从界面启动 Playwright 可见浏览器登录初始化."""
    body = body or LoginStartRequest()
    settings = get_settings()
    source = get_source("login_portal")
    configuration_error = getattr(source, "configuration_error", None)
    if configuration_error:
        raise HTTPException(status_code=400, detail=configuration_error)
    if body.login_url:
        requested_host = (urlparse(body.login_url).hostname or "").lower().removeprefix("www.")
        configured_host = (
            urlparse(settings.login_source_home_url).hostname or ""
        ).lower().removeprefix("www.")
        if not requested_host or requested_host != configured_host:
            raise HTTPException(
                status_code=400,
                detail="登录地址必须与已配置的首页和检索地址属于同一门户",
            )
    result = start_login_process(
        login_url=body.login_url,
        wait_seconds=body.wait_seconds,
        force=body.force,
    )
    if not result.ok and result.mode != "running":
        raise HTTPException(status_code=500, detail=result.message)
    return {
        "ok": result.ok or result.mode == "running",
        "message": result.message,
        "pid": result.pid,
        "wait_seconds": result.wait_seconds,
        "login_url": result.login_url,
        "mode": result.mode,
        "launcher": get_launcher_status(),
        "hint": (
            "请切换到弹出的浏览器窗口完成登录；"
            "完成后在黑色控制台按 Enter，或等待倒计时结束自动保存。"
        ),
    }


@router.post("/stop")
async def login_stop() -> dict:
    """停止登录初始化进程."""
    return {**stop_login_process(), "launcher": get_launcher_status()}


@router.delete("/state")
async def login_clear_state() -> dict:
    """清除本地 storage state，需重新登录."""
    if get_launcher_status().get("process_running"):
        stop_login_process()
    return clear_login_state()


@router.post("/open-scripts")
async def login_open_scripts() -> dict:
    """打开 scripts 目录（备用：双击 bat）."""
    return open_scripts_folder()
