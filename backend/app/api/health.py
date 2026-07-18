"""健康检查与系统元信息."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.database import check_db, database_revision
from app.schemas.health import HealthResponse, MetaResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone)
    now = datetime.now(tz)
    db_ok = await check_db()
    revision = await database_revision() if db_ok else "unavailable"
    status = "ok" if db_ok else "degraded"
    return HealthResponse(
        status=status,
        app=settings.app_name,
        version=settings.app_version,
        phase=settings.app_phase,
        timezone=settings.app_timezone,
        time=now,
        database="ok" if db_ok else "error",
        database_ok=db_ok,
        database_revision=revision,
        extraction_version="v2",
        capabilities=[
            "detail-evidence-v2",
            "interactive-detail-recrawl-v1",
            "official-document-import-v1",
            "pdfjs-text-layer-capture-v1",
            "browser-rendered-detail-capture-v1",
        ],
        message="服务正常" if db_ok else "数据库连接异常",
    )


@router.get("/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    settings = get_settings()
    return MetaResponse(
        name=settings.app_name,
        version=settings.app_version,
        phase=settings.app_phase,
        timezone=settings.app_timezone,
        language="zh-CN",
        description="AI 招投标信息聚合工具 — 自然语言驱动多源采集、清洗、去重与 Word 报告",
        features_ready=[
            "项目骨架",
            "健康检查",
            "数据模型与 SQLite",
            "基础前端页面",
            "意图解析（API/Ollama/规则）",
            "解析确认与任务保存",
            "LLM 模型选择与 Ollama 拉取",
            "公开数据源 ccgp + cebpub",
            "清洗过滤与任务执行",
            "登录态数据源（Playwright 手动登录）",
            "跨源去重与增量推送",
            "Word 报告生成与下载",
            "定时任务调度（单次/日/周/月）",
            "联调文档与 Demo 脚本",
        ],
        features_planned=[
            "更多区域站点适配",
            "消息推送渠道（邮件/IM，非赛题必选）",
        ],
    )
