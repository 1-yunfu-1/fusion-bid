"""统一招投标数据源适配器接口."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SourceDetailError(RuntimeError):
    """A source detail failure with a stable public diagnostic code."""

    def __init__(self, message: str, *, reason: str, stage: str) -> None:
        super().__init__(message)
        self.failure_reason = reason
        self.failure_stage = stage


@dataclass
class HealthResult:
    ok: bool
    message: str = ""
    requires_login: bool = False
    login_ok: bool | None = None
    checked_at: datetime | None = None


@dataclass
class SearchQuery:
    keywords: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListItem:
    title: str
    source_url: str
    source_item_id: str | None = None
    publish_time: datetime | None = None
    snippet: str | None = None
    region: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetailResult:
    title: str
    source_url: str
    publish_time: datetime | None = None
    region: str | None = None
    raw_content: str = ""
    clean_content: str = ""
    attachment_links: list[str] = field(default_factory=list)
    detail_fetched: bool = True
    # 与 detail_fetched 并存，便于区分“明确只有列表元数据”和“请求异常”。
    detail_status: str = "full"  # full | metadata_only | failed | needs_human_verification
    detail_url: str | None = None
    content_format: str | None = None  # html | json | pdf_text | pdf_ocr
    source_metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class TenderSourceAdapter(ABC):
    """所有数据源必须实现的统一接口."""

    source_name: str
    requires_login: bool = False
    enabled: bool = True

    @abstractmethod
    async def health_check(self) -> HealthResult:
        raise NotImplementedError

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[ListItem]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
        raise NotImplementedError

    @abstractmethod
    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        raise NotImplementedError
