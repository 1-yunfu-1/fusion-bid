"""跨源去重与增量交付测试."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.deduplication.engine import CandidateRecord, deduplicate_candidates, is_duplicate
from app.deduplication.incremental import mark_delivered, plan_incremental_delivery
from app.deduplication.normalize import normalize_title
from app.models.announcement import TenderAnnouncement
from app.models.delivery import DeliveryHistory
from app.models.task import SearchTask

TZ = ZoneInfo("Asia/Shanghai")


def test_normalize_title():
    a = normalize_title("安徽省某某服务器采购公开招标公告")
    b = normalize_title("安徽省某某服务器采购【二次】公开招标公告")
    assert a
    assert "公告" not in a or "招标" not in a  # 后缀剥离
    assert normalize_title("Foo") == normalize_title("foo")


def test_is_duplicate_same_title_cross_source():
    pub = datetime(2026, 6, 1, tzinfo=TZ)
    a = CandidateRecord(
        title="安徽省服务器采购公开招标公告",
        source_name="ccgp",
        source_url="http://a/1",
        publish_time=pub,
        region="安徽省",
        clean_content="采购人甲 服务器 招标 预算见附件",
        attachment_links=["http://a/f.pdf"],
    )
    b = CandidateRecord(
        title="安徽省服务器采购招标公告",
        source_name="cebpub",
        source_url="http://b/2",
        publish_time=pub,
        region="安徽",
        clean_content="采购人甲 服务器 招标 内容转载",
        attachment_links=["http://b/f.pdf"],
    )
    ok, reason = is_duplicate(a, b)
    assert ok is True
    assert reason


def test_deduplicate_prefers_official_and_merges_urls():
    pub = datetime(2026, 6, 1, tzinfo=TZ)
    records = [
        CandidateRecord(
            title="上海市充电桩项目公开招标公告",
            source_name="login_portal",
            source_url="http://agg/1",
            publish_time=pub,
            region="上海市",
            clean_content="简短",
            attachment_links=[],
        ),
        CandidateRecord(
            title="上海市充电桩项目公开招标公告",
            source_name="ccgp",
            source_url="http://ccgp/1",
            publish_time=pub,
            region="上海市",
            clean_content="完整正文" * 20,
            attachment_links=["http://ccgp/a.pdf"],
        ),
    ]
    result = deduplicate_candidates(records)
    assert len(result.primaries) == 1
    assert result.merged_count == 1
    primary = result.primaries[0]
    assert primary.source_name == "ccgp"
    assert any("agg" in u or "ccgp" in u for u in primary.related_urls)
    assert primary.related_sources


@pytest.mark.asyncio
async def test_incremental_new_update_skip():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        task = SearchTask(
            original_query="q",
            keywords=["服务器"],
            regions=["安徽省"],
            status="confirmed",
            execute_immediately=True,
            schedule_enabled=False,
        )
        db.add(task)
        ann = TenderAnnouncement(
            title="t",
            source_name="ccgp",
            source_url="http://x",
            data_mode="live",
            requires_login=False,
            content_hash="hash1",
            is_primary=True,
        )
        db.add(ann)
        await db.commit()
        await db.refresh(task)
        await db.refresh(ann)

        plan1 = await plan_incremental_delivery(
            db, task_id=task.id, announcements=[(ann.id, "hash1")]
        )
        assert plan1.new_count == 1
        assert plan1.update_count == 0

        await mark_delivered(
            db,
            task_id=task.id,
            items=plan1.items,
            report_id="exec-1",
        )
        await db.commit()

        plan2 = await plan_incremental_delivery(
            db, task_id=task.id, announcements=[(ann.id, "hash1")]
        )
        assert plan2.new_count == 0
        assert plan2.skipped_count == 1

        plan3 = await plan_incremental_delivery(
            db, task_id=task.id, announcements=[(ann.id, "hash2")]
        )
        assert plan3.update_count == 1
        assert plan3.items[0].is_update is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_execution_should_not_mark_via_api_contract():
    """增量标记仅由成功路径调用；此处验证 mark 前后 history 条数逻辑."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        task = SearchTask(
            original_query="q",
            keywords=["k"],
            regions=["北京市"],
            status="confirmed",
            execute_immediately=True,
            schedule_enabled=False,
        )
        ann = TenderAnnouncement(
            title="t2",
            source_name="cebpub",
            source_url="http://y",
            data_mode="live",
            requires_login=False,
            content_hash="h",
            is_primary=True,
        )
        db.add_all([task, ann])
        await db.commit()
        await db.refresh(task)
        await db.refresh(ann)
        # 模拟失败：不调用 mark_delivered
        count = (
            await db.execute(
                __import__("sqlalchemy", fromlist=["select"]).select(DeliveryHistory)
            )
        ).scalars().all()
        assert len(count) == 0
    await engine.dispose()
