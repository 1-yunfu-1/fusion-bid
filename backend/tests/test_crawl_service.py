"""采集编排测试（全 mock 数据源）."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.task import SearchTask
from app.models.delivery import DeliveryHistory
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


class MockMirrorSource(MockPublicSource):
    """A second source exposing the same announcement under a distinct URL."""

    source_name = "mock_mirror"

    async def search(self, query: SearchQuery) -> list[ListItem]:
        return [
            ListItem(
                title="安徽省服务器采购公开招标公告",
                source_url="http://mirror.example.com/a1",
                source_item_id="mirror-a1",
                publish_time=datetime(2026, 6, 1, tzinfo=TZ),
                region="安徽省",
                snippet="服务器 招标",
            )
        ]


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


@pytest.mark.asyncio
async def test_initial_run_keeps_scheduled_task_scheduled(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(crawl_service, "build_sources", lambda only_enabled=True: [MockPublicSource()])

    def fake_report(*args, **kwargs):
        path = tmp_path / "scheduled.docx"
        path.write_bytes(b"docx")
        return path

    monkeypatch.setattr(crawl_service, "generate_report_file", fake_report)

    async with factory() as db:
        task = SearchTask(
            original_query="安徽省服务器每日增量",
            keywords=["服务器"],
            regions=["安徽省"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 7, 17),
            execute_immediately=True,
            schedule_enabled=True,
            schedule_type="daily",
            execute_time="09:00",
            status="scheduled",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        execution, _ = await crawl_service.execute_search_task(
            db, task, trigger_type="initial"
        )
        assert execution.trigger_type == "initial"
        assert task.status == "scheduled"

    await engine.dispose()


@pytest.mark.asyncio
async def test_report_failure_does_not_write_delivery_history(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(crawl_service, "build_sources", lambda only_enabled=True: [MockPublicSource()])

    def fail_report(*args, **kwargs):
        raise RuntimeError("mock report failure")

    monkeypatch.setattr(crawl_service, "generate_report_file", fail_report)

    async with factory() as db:
        task = SearchTask(
            original_query="安徽省服务器招标",
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

        execution, _ = await crawl_service.execute_search_task(db, task)
        delivered = await db.scalar(select(func.count()).select_from(DeliveryHistory))
        assert execution.status == "partial"
        assert execution.report_path is None
        assert delivered == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_recollects_all_current_items_without_duplicate_delivery(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        crawl_service,
        "build_sources",
        lambda only_enabled=True: [MockPublicSource(), MockMirrorSource()],
    )

    def fake_report(*args, **kwargs):
        path = tmp_path / "snapshot.docx"
        path.write_bytes(b"docx")
        return path

    monkeypatch.setattr(crawl_service, "generate_report_file", fake_report)
    async with factory() as db:
        task = SearchTask(
            original_query="安徽省服务器招标",
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

        await crawl_service.execute_search_task(db, task)
        before = await db.scalar(select(func.count()).select_from(DeliveryHistory))
        _, stats = await crawl_service.execute_search_task(
            db, task, report_scope="snapshot"
        )
        after = await db.scalar(select(func.count()).select_from(DeliveryHistory))
        assert stats.report_scope == "snapshot"
        assert stats.candidates_count == 2
        assert stats.primary_count == 1  # storage and delivery remain deduplicated
        assert len(stats.output_items) == 2  # the report keeps both source records
        assert {item["source_name"] for item in stats.output_items} == {
            "mock_public",
            "mock_mirror",
        }
        assert {item["change_label"] for item in stats.output_items} == {
            "本轮原始记录（未去重）"
        }
        assert all(item["announcement_id"].startswith("snapshot:") for item in stats.output_items)
        assert all(
            item["dedupe_status"] == "保留（同批疑似重复）"
            for item in stats.output_items
        )
        assert after == before

    await engine.dispose()
