"""Fixture 数据源占位（仅用于离线测试，界面必须标注 data_mode=fixture）."""

from __future__ import annotations

from app.sources.base import (
    DetailResult,
    HealthResult,
    ListItem,
    SearchQuery,
    TenderSourceAdapter,
)


class FixtureSourcePlaceholder(TenderSourceAdapter):
    """隐藏的离线测试占位源，不加载任何假业务数据."""

    source_name = "fixture_source_placeholder"
    requires_login = False
    enabled = False
    visible = False

    async def health_check(self) -> HealthResult:
        return HealthResult(
            ok=True,
            message="Fixture 占位：未加载数据文件",
            requires_login=False,
        )

    async def search(self, query: SearchQuery) -> list[ListItem]:
        # 明确返回空列表，不伪造招投标结果
        return []

    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
        raise NotImplementedError("无 fixture 条目可解析")

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        return []
