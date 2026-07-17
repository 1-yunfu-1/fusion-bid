"""数据源注册表：启用/禁用与实例管理."""

from __future__ import annotations

from typing import Iterable

from app.sources.base import TenderSourceAdapter
from app.sources.cebpub_source import CebpubSource
from app.sources.ccgp_source import CcgpSource
from app.sources.fixture_source import FixtureSourcePlaceholder
from app.sources.login_portal_source import LoginPortalSource
from app.sources.public_source import PublicSourcePlaceholder


def build_sources(*, only_enabled: bool = True) -> list[TenderSourceAdapter]:
    sources: list[TenderSourceAdapter] = [
        CcgpSource(),
        CebpubSource(),
        LoginPortalSource(),
        PublicSourcePlaceholder(),
        FixtureSourcePlaceholder(),
    ]
    if only_enabled:
        return [s for s in sources if s.enabled]
    return sources


def get_source(name: str) -> TenderSourceAdapter | None:
    for s in build_sources(only_enabled=False):
        if s.source_name == name:
            return s
    return None


def enabled_public_sources() -> Iterable[TenderSourceAdapter]:
    for s in build_sources(only_enabled=True):
        if not s.requires_login:
            yield s
