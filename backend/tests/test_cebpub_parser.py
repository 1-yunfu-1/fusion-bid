"""CEBPUB JSON 解析测试（mock，不访问外网）."""

from __future__ import annotations

import pytest

from app.browser.pdf_detail import PublicPdfDetail
from app.sources.base import ListItem, SearchQuery
from app.sources.cebpub_source import CebpubSource


class FakeResp:
    def __init__(self, data, text=""):
        self._data = data
        self.text = text

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

    async def fake_detail_sequence(steps):
        # 不再请求/解析通用门户 GET 页；详情必须走官方 POST 链路。
        assert len(steps) == 2
        assert steps[0][1]["businessId"] == "BIZ001"
        assert steps[1][1]["businessObjectName"] == items[0].title
        return [
            FakeResp({}, "<html>公告检索首页，不含当前公告</html>"),
            FakeResp({"object": []}),
        ]

    monkeypatch.setattr(source.fetcher, "post_form_sequence", fake_detail_sequence)
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
    assert detail.detail_fetched is False
    assert detail.detail_status == "metadata_only"
    assert "公告检索" not in detail.clean_content


@pytest.mark.asyncio
async def test_cebpub_extracts_only_matching_official_detail(monkeypatch):
    source = CebpubSource()
    item = ListItem(
        title="上海市充电桩建设项目招标公告",
        source_url="https://example.test/detail?businessId=BIZ001",
        source_item_id="BIZ001",
        raw={"businessId": "BIZ001"},
    )

    async def fake_detail_sequence(steps):
        assert steps[0][0].endswith("showDetails.do")
        assert steps[1][0].endswith("findDetails.do")
        return [
            FakeResp({}, "<h1>上海市充电桩建设项目招标公告</h1>BIZ001"),
            FakeResp(
                {
                    "object": {
                        "businessId": "BIZ001",
                        "businessObjectName": "上海市充电桩建设项目招标公告",
                        "tendererName": "上海测试招标人",
                        "qualificationRequirements": "1. 具备有效营业执照\n2. 具备同类项目业绩",
                        "bulletinContent": "<p>项目正文</p>",
                    }
                }
            ),
        ]

    monkeypatch.setattr(source.fetcher, "post_form_sequence", fake_detail_sequence)
    detail = await source.fetch_detail(item)
    assert detail.detail_fetched is True
    assert detail.detail_status == "full"
    assert "项目正文" in detail.clean_content
    assert "招标人名称：上海测试招标人" in detail.clean_content
    assert "投标人资格要求：1. 具备有效营业执照" in detail.clean_content


@pytest.mark.asyncio
async def test_cebpub_rejects_mismatched_detail_payload(monkeypatch):
    source = CebpubSource()
    item = ListItem(
        title="上海市充电桩建设项目招标公告",
        source_url="https://example.test/detail?businessId=BIZ001",
        source_item_id="BIZ001",
        raw={"businessId": "BIZ001"},
    )

    async def fake_detail_sequence(steps):
        return [
            FakeResp({}, "<h1>上海市充电桩建设项目招标公告</h1>BIZ001"),
            FakeResp(
                {
                    "object": {
                        "businessId": "OTHER",
                        "businessObjectName": "另一项目招标公告",
                        "bulletinContent": "<p>通用门户内容</p>",
                    }
                }
            ),
        ]

    monkeypatch.setattr(source.fetcher, "post_form_sequence", fake_detail_sequence)
    detail = await source.fetch_detail(item)
    assert detail.detail_status == "metadata_only"
    assert "通用门户内容" not in detail.clean_content


@pytest.mark.asyncio
async def test_cebpub_accepts_detail_nested_under_verified_identity(monkeypatch):
    source = CebpubSource()
    item = ListItem(
        title="上海市充电桩建设项目招标公告",
        source_url="https://example.test/detail?businessId=BIZ001",
        source_item_id="BIZ001",
        raw={"businessId": "BIZ001"},
    )

    async def fake_detail_sequence(steps):
        return [
            FakeResp({}, "<h1>上海市充电桩建设项目招标公告</h1>BIZ001"),
            FakeResp(
                {
                    "object": {
                        "businessId": "BIZ001",
                        "businessObjectName": "上海市充电桩建设项目招标公告",
                        "detail": {
                            "tendererName": "上海测试招标人",
                            "bulletinContent": "<p>嵌套详情正文</p>",
                        },
                    }
                }
            ),
        ]

    monkeypatch.setattr(source.fetcher, "post_form_sequence", fake_detail_sequence)
    detail = await source.fetch_detail(item)
    assert detail.detail_status == "full"
    assert "嵌套详情正文" in detail.clean_content


@pytest.mark.asyncio
async def test_cebpub_interactive_fetch_uses_visible_browser(monkeypatch):
    source = CebpubSource()
    business_id = "1d1600b68217477890a8076bc98a6880"
    item = ListItem(
        title="服务器、数据库、数据库集群软件招标公告",
        source_url="https://ctbpsp.com/",
        source_item_id=business_id,
        raw={"businessId": business_id},
    )
    calls = []

    async def fake_pdf_detail(**kwargs):
        calls.append(kwargs)
        return PublicPdfDetail(
            status="needs_human_verification",
            detail_url=kwargs["detail_url"],
            message="等待人工验证",
            failure_reason="verification_timeout",
        )

    monkeypatch.setattr(
        "app.sources.cebpub_source.fetch_public_pdf_detail", fake_pdf_detail
    )
    detail = await source.fetch_detail(item, interactive=True)

    assert calls[0]["headless"] is False
    assert calls[0]["managed"] is True
    assert calls[0]["timeout_ms"] == 300_000
    assert detail.detail_status == "needs_human_verification"
    assert detail.source_metadata["acquisition_mode"] == "managed_chrome"
    assert detail.source_metadata["interaction_requested"] is True
    assert detail.source_metadata["failure_reason"] == "verification_timeout"


@pytest.mark.asyncio
async def test_cebpub_keeps_managed_browser_metadata_only_failure(monkeypatch):
    source = CebpubSource()
    business_id = "1d1600b68217477890a8076bc98a6880"
    item = ListItem(
        title="服务器、数据库、数据库集群软件招标公告",
        source_url="https://ctbpsp.com/",
        source_item_id=business_id,
        raw={"businessId": business_id},
    )

    async def fake_pdf_detail(**kwargs):
        return PublicPdfDetail(
            status="metadata_only",
            detail_url=kwargs["detail_url"],
            message="PDF 第 2 页未能读取",
            failure_reason="incomplete_pdf_pages",
            acquisition_mode="managed_chrome",
            browser_reused=True,
            browser_state="ready",
        )

    async def legacy_must_not_run(_steps):
        raise AssertionError("32 位 UUID 不应回退到旧详情接口")

    monkeypatch.setattr(
        "app.sources.cebpub_source.fetch_public_pdf_detail", fake_pdf_detail
    )
    monkeypatch.setattr(source.fetcher, "post_form_sequence", legacy_must_not_run)

    detail = await source.fetch_detail(item)

    assert detail.detail_status == "metadata_only"
    assert detail.source_metadata["failure_reason"] == "incomplete_pdf_pages"
    assert detail.source_metadata["acquisition_mode"] == "managed_chrome"
    assert detail.source_metadata["browser_reused"] is True
