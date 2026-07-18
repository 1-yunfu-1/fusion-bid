"""数据源状态与健康检查 API."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.sources.registry import build_sources, get_source

router = APIRouter(prefix="/sources", tags=["sources"])
TZ = ZoneInfo("Asia/Shanghai")


@router.get("")
async def list_sources() -> dict:
    items = []
    for s in build_sources(only_enabled=False):
        if not getattr(s, "visible", True):
            continue
        items.append(
            {
                "source_name": s.source_name,
                "display_name": getattr(s, "display_name", s.source_name),
                "requires_login": s.requires_login,
                "enabled": s.enabled,
                "official": getattr(s, "official", False),
                "data_mode": getattr(s, "data_mode", "live"),
            }
        )
    return {"items": items, "checked_at": datetime.now(TZ).isoformat()}


@router.post("/{source_name}/health")
async def source_health(source_name: str) -> dict:
    source = get_source(source_name)
    if not source:
        raise HTTPException(status_code=404, detail="未知数据源")
    result = await source.health_check()
    return {
        "source_name": source.source_name,
        "ok": result.ok,
        "message": result.message,
        "requires_login": result.requires_login,
        "login_ok": result.login_ok,
        "checked_at": (result.checked_at or datetime.now(TZ)).isoformat(),
    }


@router.post("/health-all")
async def health_all() -> dict:
    results = []
    for s in build_sources(only_enabled=False):
        if not getattr(s, "visible", True):
            continue
        try:
            r = await s.health_check()
            results.append(
                {
                    "source_name": s.source_name,
                    "ok": r.ok,
                    "message": r.message,
                    "requires_login": r.requires_login,
                    "login_ok": r.login_ok,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "source_name": s.source_name,
                    "ok": False,
                    "message": str(exc),
                    "requires_login": s.requires_login,
                }
            )
    return {"results": results, "checked_at": datetime.now(TZ).isoformat()}
