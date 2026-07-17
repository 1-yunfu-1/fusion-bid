"""数据源适配器接口契约测试."""

import pytest

from app.sources.base import TenderSourceAdapter
from app.sources.cebpub_source import CebpubSource
from app.sources.ccgp_source import CcgpSource
from app.sources.fixture_source import FixtureSourcePlaceholder
from app.sources.login_portal_source import LoginPortalSource
from app.sources.public_source import PublicSourcePlaceholder
from app.sources.registry import build_sources


def test_adapters_are_subclasses():
    assert issubclass(CcgpSource, TenderSourceAdapter)
    assert issubclass(CebpubSource, TenderSourceAdapter)
    assert issubclass(LoginPortalSource, TenderSourceAdapter)
    assert issubclass(FixtureSourcePlaceholder, TenderSourceAdapter)


def test_enabled_sources_include_public_and_login():
    all_names = {s.source_name for s in build_sources(only_enabled=False)}
    assert "ccgp" in all_names
    assert "cebpub" in all_names
    assert "login_portal" in all_names


@pytest.mark.asyncio
async def test_placeholders_do_not_return_fake_tenders():
    public = PublicSourcePlaceholder()
    fixture = FixtureSourcePlaceholder()
    assert public.enabled is False
    items = await public.search(
        __import__("app.sources.base", fromlist=["SearchQuery"]).SearchQuery()
    )
    assert items == []
    fitems = await fixture.search(
        __import__("app.sources.base", fromlist=["SearchQuery"]).SearchQuery()
    )
    assert fitems == []


@pytest.mark.asyncio
async def test_login_source_requires_login_flag():
    src = LoginPortalSource()
    assert src.requires_login is True
    h = await src.health_check()
    assert h.requires_login is True
