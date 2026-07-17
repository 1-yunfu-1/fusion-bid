"""采集编排测试（全 mock 数据源）."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.task import SearchTask
from app.services import crawl_service
from app.sources.base import DetailResult, HealthResult, ListItem, SearchQuery, TenderSourceAdapter

TZ = ZoneInfo("Asia/Shanghai")


class MockPublicSource(TenderSourceAdapter):
    source_name = "mock_public"
    requires_login = False
    enabled = True

    async def health_check(self) -> HealthResult:
        return HealthResult(ok=True, message="ok")

    async def search(self, query: SearchQuery) -> list[ListItem]:
        return [
            ListItem(
                title="安徽省服务器采购公开招标公告",
                source_url="http://example.com/a1",
                source_item_id="a1",
                publish_time=datetime(2026, 6, 1, tzinfo=TZ),
                region="安徽省",
                snippet="服务器 招标",
            ),
            ListItem(
                title="培训班招生广告",
                source_url="http://example.com/noise",
                source_item_id="n1",
                publish_time=datetime(2026, 6, 2, tzinfo=TZ),
                region="安徽省",
            ),
        ]

    async def fetch_detail(self, item: ListItem) -> DetailResult:
        return DetailResult(
            title=item.title,
            source_url=item.source_url,
            publish_time=item.publish_time,
            region=item.region,
            raw_content="<p>采购人：测试单位</p><p>安徽省 服务器 招标</p>",
            clean_content="采购人：测试单位\n安徽省 服务器 公开招标\n项目所在地：安徽省",
            attachment_links=[],
        )

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_execute_search_task_saves_announcements(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(crawl_service, "build_sources", lambda only_enabled=True: [MockPublicSource()])

    async with factory() as db:
        task = SearchTask(
            original_query="最近1个月安徽省服务器招标",
            keywords=["服务器"],
            regions=["安徽省"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 7, 17),
            execute_immediately=True,
            schedule_enabled=False,
            status="confirmed",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        execution, stats = await crawl_service.execute_search_task(db, task, max_details_per_source=5)
        assert execution.status in ("success", "partial")
        assert stats.saved_count >= 1
        assert "mock_public" in stats.sources_succeeded

    await engine.dispose()
