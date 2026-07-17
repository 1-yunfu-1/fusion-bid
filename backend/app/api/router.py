"""聚合 API 路由."""

from fastapi import APIRouter

from app.api import announcements, health, llm, login, parse, reports, sources, tasks

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(parse.router)
api_router.include_router(tasks.router)
api_router.include_router(llm.router)
api_router.include_router(sources.router)
api_router.include_router(announcements.router)
api_router.include_router(login.router)
api_router.include_router(reports.router)
