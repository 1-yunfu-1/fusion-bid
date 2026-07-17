"""登录态数据源与会话逻辑测试（不启动真实浏览器、不访问外网）."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.browser.session import (
    LoginRequiredError,
    looks_like_login_wall,
    looks_like_logged_in,
    safe_state_meta,
)
from app.services import crawl_service
from app.sources.base import DetailResult, HealthResult, ListItem, SearchQuery, TenderSourceAdapter
from app.sources.login_portal_source import LoginPortalSource


def test_login_wall_detection():
    assert looks_like_login_wall("<html>请登录后查看完整信息</html>") is True
    assert looks_like_logged_in("<html>欢迎，个人中心 退出登录</html>") is True
    assert looks_like_login_wall("<html>招标公告列表 服务器采购</html>") is False


def test_safe_state_meta_no_file(tmp_path, monkeypatch):
    from app.browser import session as sess

    monkeypatch.setattr(sess, "state_file_path", lambda filename=None: tmp_path / "x.json")
    meta = safe_state_meta(tmp_path / "x.json")
    assert meta["exists"] is False
    assert "cookies" not in meta


@pytest.mark.asyncio
async def test_login_source_search_without_state(tmp_path, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LOGIN_SOURCE_ENABLED", "true")
    get_settings.cache_clear()

    src = LoginPortalSource()
    src.state_path = tmp_path / "missing.json"
    src.enabled = True
    with pytest.raises(LoginRequiredError):
        await src.search(SearchQuery(keywords=["服务器"], regions=["安徽省"]))


@pytest.mark.asyncio
async def test_login_source_parse_list_from_html():
    src = LoginPortalSource()
    html = """
    <html><body>
      <a href="/detail/1">安徽省服务器采购公开招标公告项目</a>
      <a href="/login">请登录</a>
      <a href="/">首页</a>
    </body></html>
    """
    items = src._parse_list(html, base_url="https://example.com/", keyword="服务器")
    assert any("服务器" in i.title for i in items)


@pytest.mark.asyncio
async def test_crawl_skips_login_failure_keeps_public(monkeypatch):
    """登录源失败时公开源仍可成功."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.database import Base
    from app.models.task import SearchTask

    TZ = ZoneInfo("Asia/Shanghai")

    class OkPublic(TenderSourceAdapter):
        source_name = "mock_public"
        requires_login = False
        enabled = True

        async def health_check(self) -> HealthResult:
            return HealthResult(ok=True)

        async def search(self, query: SearchQuery) -> list[ListItem]:
            return [
                ListItem(
                    title="安徽省服务器采购公开招标公告",
                    source_url="http://example.com/p1",
                    source_item_id="p1",
                    publish_time=datetime(2026, 6, 1, tzinfo=TZ),
                    region="安徽省",
                    snippet="服务器 招标",
                )
            ]

        async def fetch_detail(self, item: ListItem) -> DetailResult:
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                publish_time=item.publish_time,
                region=item.region,
                raw_content="x",
                clean_content="安徽省 服务器 公开招标 采购人：测试",
                attachment_links=[],
            )

        async def extract_attachments(self, detail: DetailResult) -> list[str]:
            return []

    class BadLogin(TenderSourceAdapter):
        source_name = "mock_login"
        requires_login = True
        enabled = True

        async def health_check(self) -> HealthResult:
            return HealthResult(ok=False, message="未登录", requires_login=True, login_ok=False)

        async def search(self, query: SearchQuery) -> list[ListItem]:
            raise LoginRequiredError("should not be called")

        async def fetch_detail(self, item: ListItem) -> DetailResult:
            raise NotImplementedError

        async def extract_attachments(self, detail: DetailResult) -> list[str]:
            return []

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(
        crawl_service,
        "build_sources",
        lambda only_enabled=True: [OkPublic(), BadLogin()],
    )

    async with factory() as db:
        task = SearchTask(
            original_query="测试",
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
        execution, stats = await crawl_service.execute_search_task(db, task)
        assert "mock_public" in stats.sources_succeeded
        assert "mock_login" in stats.sources_failed
        assert stats.saved_count >= 1
        assert execution.status in ("success", "partial")

    await engine.dispose()


@pytest.mark.asyncio
async def test_login_status_api(client):
    resp = await client.get("/api/login/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "instructions" in data
    assert "state" in data
    assert "cookies" not in str(data).lower() or data["state"].get("exists") is False
