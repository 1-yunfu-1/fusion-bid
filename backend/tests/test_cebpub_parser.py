"""CEBPUB JSON 解析测试（mock，不访问外网）."""

from __future__ import annotations

import pytest

from app.sources.base import SearchQuery
from app.sources.cebpub_source import CebpubSource


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.mark.asyncio
async def test_cebpub_search_mock(monkeypatch):
    source = CebpubSource()
    source.max_pages = 1

    payload = {
        "success": True,
        "object": {
            "returnlist": [
                {
                    "businessObjectName": "上海市充电桩建设项目招标公告",
                    "businessId": "BIZ001",
                    "regionName": "上海市",
                    "receiveTime": "2026-03-10",
                    "bulletinEndTime": "2026-03-20",
                    "transactionPlatfName": "某交易中心",
                    "tenderProjectCode": "SH-2026-001",
                    "industriesType": "市政",
                }
            ],
            "page": {"totalCount": 1},
        },
    }

    async def fake_post(url, data):
        return FakeResp(payload)

    monkeypatch.setattr(source.fetcher, "post_form", fake_post)
    items = await source.search(
        SearchQuery(
            keywords=["充电桩"],
            regions=["上海市"],
            start_date="2026-01-01",
            end_date="2026-06-30",
        )
    )
    assert len(items) == 1
    assert items[0].source_item_id == "BIZ001"
    assert "充电桩" in items[0].title
    detail = await source.fetch_detail(items[0])
    assert "项目名称" in detail.clean_content
    assert detail.attachment_links == []
