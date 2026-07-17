"""FusionBid FastAPI 入口."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import init_db
from app.scheduler.manager import restore_jobs_from_db, shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)


def _frontend_dist() -> Path | None:
    """发布包或开发构建产物：项目根 frontend/dist."""
    dist = PROJECT_ROOT / "frontend" / "dist"
    if (dist / "index.html").is_file():
        return dist
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    await init_db()
    # 测试环境可不启动真实调度器（由 APP_ENV=test 控制）
    if settings.app_env != "test":
        start_scheduler()
        try:
            n = await restore_jobs_from_db()
            logger.info("restored %s scheduled jobs", n)
        except Exception:  # noqa: BLE001
            logger.exception("restore scheduled jobs failed")
    yield
    if settings.app_env != "test":
        shutdown_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="FusionBid 智标聚合助手 API",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router)

    # 测试环境保持 JSON 根路由，避免与 test_root 冲突；发布/开发有 dist 时托管 SPA
    dist = _frontend_dist() if settings.app_env != "test" else None

    if dist is None:
        @application.get("/")
        async def root():
            return {
                "name": settings.app_name,
                "version": settings.app_version,
                "phase": settings.app_phase,
                "docs": "/docs",
                "health": "/api/health",
                "message": "前端未构建。请执行 frontend 下 npm run build，或使用发布包中的 start.bat",
            }
    else:
        assets = dist / "assets"
        if assets.is_dir():
            application.mount(
                "/assets",
                StaticFiles(directory=str(assets)),
                name="frontend-assets",
            )

        @application.get("/")
        async def spa_index():
            return FileResponse(dist / "index.html")

        @application.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # 不拦截 API / 文档 / OpenAPI
            if full_path.startswith(
                ("api/", "docs", "redoc", "openapi.json", "assets/")
            ):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = dist / full_path
            if candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

        logger.info("serving frontend SPA from %s", dist)

    return application


app = create_app()
