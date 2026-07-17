"""公开数据源占位（阶段三实现真实抓取，阶段一不返回伪造业务数据）."""

from __future__ import annotations

from app.sources.base import (
    DetailResult,
    HealthResult,
    ListItem,
    SearchQuery,
    TenderSourceAdapter,
)


class PublicSourcePlaceholder(TenderSourceAdapter):
    """历史占位：真实公开源见 ccgp_source / cebpub_source."""

    source_name = "public_source_placeholder"
    requires_login = False
    enabled = False

    async def health_check(self) -> HealthResult:
        return HealthResult(
            ok=True,
            message="已弃用占位；请使用 ccgp / cebpub",
            requires_login=False,
        )

    async def search(self, query: SearchQuery) -> list[ListItem]:
        return []

    async def fetch_detail(self, item: ListItem) -> DetailResult:
        raise NotImplementedError("请使用 ccgp 或 cebpub 适配器")

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        return []
