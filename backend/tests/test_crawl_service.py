"""采集编排测试（全 mock 数据源）."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.task import SearchTask
from app.models.delivery import DeliveryHistory
from app.models.announcement import TenderAnnouncement
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

    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
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


class ParallelCebpubSource(TenderSourceAdapter):
    source_name = "cebpub"
    requires_login = False
    enabled = True

    def __init__(self, state: dict, *, blocked: bool = False) -> None:
        self.state = state
        self.blocked = blocked

    async def health_check(self) -> HealthResult:
        return HealthResult(ok=True)

    async def search(self, query: SearchQuery) -> list[ListItem]:
        names = ("甲型", "乙型", "丙型", "丁型")
        return [
            ListItem(
                title=f"安徽省{value}服务器采购项目招标公告",
                source_url=f"https://ctbpsp.com/#/bulletinDetail?uuid={index:032x}",
                source_item_id=f"{index:032x}",
                publish_time=datetime(2026, 6, index + 1, tzinfo=TZ),
                region="安徽省",
                snippet=f"安徽省 {value} 服务器 招标",
            )
            for index, value in enumerate(names, start=1)
        ]

    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
        self.state["fetch_count"] = self.state.get("fetch_count", 0) + 1
        self.state["detail_active"] = self.state.get("detail_active", 0) + 1
        self.state["max_detail_active"] = max(
            self.state.get("max_detail_active", 0), self.state["detail_active"]
        )
        if self.state.get("ai_active", 0):
            self.state["overlap"] = True
        await asyncio.sleep(0.04)
        self.state["detail_active"] -= 1
        if self.blocked:
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                publish_time=item.publish_time,
                region=item.region,
                clean_content=f"{item.title}\n安徽省服务器",
                detail_fetched=False,
                detail_status="needs_human_verification",
                detail_url=item.source_url,
                source_metadata={
                    "detail_attempt_state": "blocked",
                    "failure_reason": "verification_required",
                    "failure_stage": "outer_page",
                    "site_blocked": True,
                },
            )
        return DetailResult(
            title=item.title,
            source_url=item.source_url,
            publish_time=item.publish_time,
            region=item.region,
            clean_content=(
                f"采购人：测试单位{item.source_item_id[-1:]}\n"
                f"项目编号：CODE-{item.source_item_id[-4:]}\n安徽省服务器公开招标"
            ),
            detail_fetched=True,
            detail_status="full",
            detail_url=item.source_url,
            content_format="pdf_text",
            source_metadata={"detail_attempt_state": "attempted"},
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


@pytest.mark.asyncio
async def test_bounded_pipeline_runs_two_cebpub_and_two_ai_jobs_with_overlap(
    monkeypatch, tmp_path
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    state: dict[str, int | bool] = {}
    source = ParallelCebpubSource(state)
    monkeypatch.setattr(
        crawl_service, "build_sources", lambda only_enabled=True: [source]
    )

    async def fake_ai(**kwargs):
        state["ai_active"] = int(state.get("ai_active", 0)) + 1
        state["max_ai_active"] = max(
            int(state.get("max_ai_active", 0)), int(state["ai_active"])
        )
        if state.get("detail_active", 0):
            state["overlap"] = True
        await asyncio.sleep(0.06)
        state["ai_active"] = int(state["ai_active"]) - 1
        return crawl_service.build_extraction_data(**kwargs)

    monkeypatch.setattr(crawl_service, "build_extraction_data_with_ai", fake_ai)

    def fake_report(*_args, **_kwargs):
        path = tmp_path / "parallel.docx"
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

        execution, stats = await crawl_service.execute_search_task(
            db, task, max_details_per_source=4
        )

        assert state["max_detail_active"] == 2
        assert state["max_ai_active"] == 2
        assert state["overlap"] is True
        assert stats.detail_success_count == 4
        assert stats.candidates_count == 4
        assert stats.effective_concurrency["cebpub_browser"] == 2
        assert stats.effective_concurrency["llm_extraction"] == 2
        assert execution.crawl_diagnostics["stage_durations_ms"]["total_wall"] > 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_http_detail_limit_is_global_across_public_sources(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    state = {"active": 0, "maximum": 0}

    class ParallelHttpSource(TenderSourceAdapter):
        requires_login = False
        enabled = True

        def __init__(self, source_name: str) -> None:
            self.source_name = source_name

        async def health_check(self) -> HealthResult:
            return HealthResult(ok=True)

        async def search(self, _query: SearchQuery) -> list[ListItem]:
            return [
                ListItem(
                    title=f"安徽省服务器采购项目{i}招标公告",
                    source_url=f"https://{self.source_name}.example/{i}",
                    source_item_id=f"{self.source_name}-{i}",
                    publish_time=datetime(2026, 6, i + 1, tzinfo=TZ),
                    region="安徽省",
                    snippet="安徽省 服务器 招标",
                )
                for i in range(3)
            ]

        async def fetch_detail(
            self, item: ListItem, *, interactive: bool = False
        ) -> DetailResult:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            await asyncio.sleep(0.03)
            state["active"] -= 1
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                publish_time=item.publish_time,
                region=item.region,
                clean_content="采购人：测试单位\n安徽省服务器招标",
                detail_fetched=True,
                detail_status="full",
            )

        async def extract_attachments(self, _detail: DetailResult) -> list[str]:
            return []

    sources = [ParallelHttpSource("http_a"), ParallelHttpSource("http_b")]
    monkeypatch.setattr(crawl_service, "build_sources", lambda only_enabled=True: sources)
    monkeypatch.setattr(
        crawl_service,
        "build_extraction_data_with_ai",
        lambda **kwargs: asyncio.sleep(
            0, result=crawl_service.build_extraction_data(**kwargs)
        ),
    )

    def fake_report(*_args, **_kwargs):
        path = tmp_path / "http-parallel.docx"
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
            status="confirmed",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        _, stats = await crawl_service.execute_search_task(
            db, task, max_details_per_source=3
        )

        assert state["maximum"] == 3
        assert stats.detail_success_count == 6
        assert stats.effective_concurrency["http_detail"] == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_cebpub_site_block_downgrades_then_marks_remaining_not_attempted(
    monkeypatch, tmp_path
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    state: dict[str, int | bool] = {}
    source = ParallelCebpubSource(state, blocked=True)
    monkeypatch.setattr(
        crawl_service, "build_sources", lambda only_enabled=True: [source]
    )

    def fake_report(*_args, **_kwargs):
        path = tmp_path / "blocked.docx"
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

        execution, stats = await crawl_service.execute_search_task(
            db, task, max_details_per_source=4
        )

        assert state["fetch_count"] == 2
        assert stats.detail_human_verification_count == 2
        assert stats.detail_not_attempted_count == 2
        assert stats.failure_breakdown == {"site_blocked": 2, "not_attempted": 2}
        assert stats.effective_concurrency["cebpub_adaptive"] is True
        assert stats.effective_concurrency["cebpub_circuit_open"] is True
        assert execution.status == "partial"

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_current_detail_preserves_and_labels_cached_full_text(
    monkeypatch, tmp_path
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class CachedFailureSource(TenderSourceAdapter):
        source_name = "cached_source"
        requires_login = False
        enabled = True

        async def health_check(self) -> HealthResult:
            return HealthResult(ok=True)

        async def search(self, _query: SearchQuery) -> list[ListItem]:
            return [
                ListItem(
                    title="安徽省服务器采购项目招标公告",
                    source_url="https://example.com/cached-a1",
                    source_item_id="a1",
                    publish_time=datetime(2026, 6, 1, tzinfo=TZ),
                    region="安徽省",
                    snippet="安徽省服务器招标",
                )
            ]

        async def fetch_detail(
            self, item: ListItem, *, interactive: bool = False
        ) -> DetailResult:
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                publish_time=item.publish_time,
                region=item.region,
                clean_content="安徽省服务器招标（仅列表元数据）",
                detail_fetched=False,
                detail_status="metadata_only",
                source_metadata={
                    "detail_attempt_state": "attempted",
                    "failure_reason": "outer_detail_unavailable",
                    "failure_stage": "outer_page",
                    "message": "官方外层页暂未返回详情",
                    "attempt_count": 2,
                    "duration_ms": 1200,
                },
            )

        async def extract_attachments(self, _detail: DetailResult) -> list[str]:
            return []

    monkeypatch.setattr(
        crawl_service,
        "build_sources",
        lambda only_enabled=True: [CachedFailureSource()],
    )

    def fake_report(*_args, **_kwargs):
        path = tmp_path / "cached.docx"
        path.write_bytes(b"docx")
        return path

    monkeypatch.setattr(crawl_service, "generate_report_file", fake_report)

    async with factory() as db:
        historical_content = "招标人：历史已核验单位\n资格要求：具备服务器项目能力"
        existing = TenderAnnouncement(
            title="安徽省服务器采购项目招标公告",
            source_name="cached_source",
            source_url="https://example.com/cached-a1",
            source_item_id="a1",
            data_mode="live",
            publish_time=datetime(2026, 6, 1, tzinfo=TZ),
            region="安徽省",
            clean_content=historical_content,
            raw_content=historical_content,
            detail_status="full",
            content_format="html",
            source_metadata={"detail_status": "full", "detail_fetched": True},
            extraction_data={},
            attachment_links=[],
            content_hash="historical-full-hash",
            deduplication_key="cached_source:a1",
            is_primary=True,
            crawl_time=datetime(2026, 6, 1, tzinfo=TZ),
            first_seen_at=datetime(2026, 6, 1, tzinfo=TZ),
            last_seen_at=datetime(2026, 6, 1, tzinfo=TZ),
        )
        task = SearchTask(
            original_query="安徽省服务器招标",
            keywords=["服务器"],
            regions=["安徽省"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 7, 17),
            status="confirmed",
        )
        db.add_all([existing, task])
        await db.commit()
        await db.refresh(task)

        execution, stats = await crawl_service.execute_search_task(
            db, task, max_details_per_source=1
        )
        saved = await db.scalar(
            select(TenderAnnouncement).where(
                TenderAnnouncement.source_item_id == "a1"
            )
        )

        assert saved is not None
        assert saved.detail_status == "full"
        assert saved.clean_content == historical_content
        assert saved.content_hash == "historical-full-hash"
        assert saved.source_metadata["using_cached_full"] is True
        assert saved.source_metadata["last_attempt"]["failure_reason"] == (
            "outer_detail_unavailable"
        )
        assert stats.cached_full_reused_count == 1
        assert stats.failure_breakdown_by_source == {
            "cached_source": {"outer_detail_unavailable": 1}
        }
        assert stats.source_detail_breakdown == {
            "cached_source": {"metadata_only": 1}
        }
        assert stats.output_items[0]["cached_full_reused"] is True
        assert stats.output_items[0]["detail_fetched"] is True
        assert stats.output_items[0]["current_attempt_detail_fetched"] is False
        assert execution.crawl_diagnostics["cached_full_reused_count"] == 1

    await engine.dispose()
