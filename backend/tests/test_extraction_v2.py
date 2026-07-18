"""详情抽取 v2 与证据强校验回归。"""

from __future__ import annotations

import pytest

from app.browser.pdf_detail import _extract_rendered_text_pages, restore_reading_order
from app.deduplication.engine import CandidateRecord, is_duplicate
from app.reports.fields import (
    _ai_source_chunks,
    _validate_ai_extraction_rows,
    build_extraction_data,
    enrich_report_item,
)


SAMPLE = """
【第1页】
服务器、数据库、数据库集群软件招标公告
1. 招标条件
本招标项目已由西安航天动力试验技术研究所部门批准建设，项目业主为西安航天动力试验技术研究所，建设资金及出资比例为国有资金100.0%，招标人为西安航天动力试验技术研究所。项目已具备招标条件，陕西铭源项目管理有限公司受招标人委托，现对该项目所需货物进行公开招标。
2. 招标内容、交货期、交货地点及招标文件售价
项目编号：C1100000189017141002
招标文件售价人民币（元）：800.0
3. 投标人资格要求
3.1 投标人须具备以下资质条件，并具备承担本招标项目的相应能力。（1）投标人应具备依法承担民事责任的能力，在中华人民共和国境内依法注册的法人或其他组织，提供有效的营业执照（或其他组织依法注册证明）。（2）投标人不得在“信用中国”网站中被列入重大税收违法失信主体，不得在“中国执行信息公开网”中被列为失信被执行人，不得在国家企业信用信息公示系统中被列入严重违法失信名单。
3.2 本项目不允许联合体投标。
3.3 本次招标允许代理商投标。代理商投标要求：/
3.4 投标人必须在航天电子采购平台完成注册，交纳投标保证金并办理CA数字证书与电子签章。
【第2页】
4. 招标文件的获取
招标文件的获取时间为2026年7月16日22时00分至2026年7月23日22时00分。
5. 投标文件的递交
投标文件递交的截止时间（投标截止时间，下同）为2026年8月6日14时00分。
开标时间为2026年8月6日14时00分。
交易平台：中国航天科技电子采购平台
""".strip()


def test_sample_extracts_tenderer_qualification_and_strict_semantics():
    extraction = build_extraction_data(
        title="服务器、数据库、数据库集群软件招标公告",
        clean_content=SAMPLE,
        project_code="C1100000189017141002",
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": SAMPLE}]},
    )
    fields = extraction["fields"]
    assert extraction["version"] == 2
    assert fields["purchaser"] == "西安航天动力试验技术研究所"
    assert fields["purchaser_source_label"] == "招标人"
    assert fields["agency"] == "陕西铭源项目管理有限公司"
    assert fields["transaction_platform"] == "中国航天科技电子采购平台"
    assert fields["agency"] != fields["transaction_platform"]
    assert len(fields["qualification_items"]) == 4
    assert "信用中国" in fields["qualification_items"][0]
    assert "严重违法失信名单" in fields["qualification_items"][0]
    assert fields["joint_venture_allowed"] == "不允许"
    assert fields["agent_allowed"] == "允许"
    assert fields["platform_registration_required"] == "需要"
    assert fields["ca_required"] == "需要"
    assert fields["document_acquisition_end"] == "2026年7月23日 22:00"
    assert fields["bid_deadline"] == "2026年8月6日 14:00"
    assert fields["opening_time"] == "2026年8月6日 14:00"
    assert fields["document_price"] == "800元"
    assert fields["budget"] == "原文未明确说明"
    assert "国有资金" in fields["funding_source"]
    assert extraction["evidence"]["purchaser"]["page"] == 1
    assert extraction["evidence"]["qualification"]["page"] == 1
    assert extraction["evidence"]["qualification"]["quote"].startswith(
        "3. 投标人资格要求"
    )


def test_missing_detail_is_not_reported_as_originally_unspecified():
    extraction = build_extraction_data(
        title="某服务器招标公告",
        clean_content="项目编号：X-001\n说明：本条仅使用列表元数据",
        project_code="X-001",
        detail_status="metadata_only",
    )
    assert extraction["fields"]["purchaser"] == "详情未获取，无法提取"
    enriched = enrich_report_item(
        {
            "title": "某服务器招标公告",
            "detail_status": "metadata_only",
            "detail_fetched": False,
            "extraction_data": extraction,
        },
        keywords=["服务器"],
        regions=[],
        start_date=None,
        end_date=None,
    )
    assert enriched["completeness"]["percent"] is None
    assert enriched["completeness"]["label"].startswith("不可评估")


def test_ai_value_without_exact_source_evidence_is_rejected():
    valid, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "purchaser",
                    "value": "被编造的采购人",
                    "source_label": "招标人",
                    "quote": "招标人为被编造的采购人",
                    "page": 1,
                }
            ]
        },
        clean_content=SAMPLE,
        source_metadata={"content_pages": [{"page": 1, "text": SAMPLE}]},
    )
    assert valid == []
    assert errors


def test_pdf_reading_order_removes_same_coordinate_duplicates():
    text = restore_reading_order(
        [
            {"text": "招标人：", "x": 10, "y": 100, "width": 40},
            {"text": "某公司", "x": 55, "y": 100, "width": 35},
            {"text": "某公司", "x": 55, "y": 100, "width": 35},
            {"text": "资格要求", "x": 10, "y": 80, "width": 40},
        ]
    )
    assert text == "招标人：某公司\n资格要求"


def test_pdf_reading_order_removes_subpixel_render_duplicates():
    text = restore_reading_order(
        [
            {"text": "招标文件售价", "x": 98.9, "y": 100, "width": 80},
            {"text": "招标文件售价", "x": 98.4, "y": 100, "width": 80},
            {"text": "800.0", "x": 190, "y": 100, "width": 30},
        ]
    )
    assert text == "招标文件售价800.0"


class _FakeLayer:
    def __init__(self, items):
        self.items = items

    async def wait_for(self, **_kwargs):
        return None

    async def evaluate(self, _script):
        return self.items

    async def inner_text(self, **_kwargs):
        return ""


class _FakePage:
    def __init__(self, items):
        self.layer = _FakeLayer(items)
        self.scrolled = False

    async def wait_for(self, **_kwargs):
        return None

    async def scroll_into_view_if_needed(self, **_kwargs):
        self.scrolled = True

    def locator(self, selector):
        assert selector == ".textLayer"
        return self.layer


class _FakePages:
    def __init__(self, pages):
        self.pages = pages

    @property
    def first(self):
        return self.pages[0]

    async def count(self):
        return len(self.pages)

    def nth(self, index):
        return self.pages[index]


class _FakeFrame:
    def __init__(self, pages):
        self.pages = _FakePages(pages)

    def locator(self, selector):
        assert selector == ".page"
        return self.pages


@pytest.mark.asyncio
async def test_pdfjs_text_layer_fallback_scrolls_and_extracts_every_page():
    rendered_pages = [
        _FakePage(
            [
                {"text": "招标人：", "x": 10, "y": 100, "width": 40},
                {"text": "某研究所", "x": 55, "y": 100, "width": 50},
            ]
        ),
        _FakePage(
            [
                {"text": "资格要求：", "x": 10, "y": 100, "width": 50},
                {"text": "具备营业执照", "x": 65, "y": 100, "width": 70},
            ]
        ),
    ]

    pages, page_count = await _extract_rendered_text_pages(
        _FakeFrame(rendered_pages), timeout_ms=1_000
    )

    assert page_count == 2
    assert [page["page"] for page in pages] == [1, 2]
    assert pages[0]["text"] == "招标人：某研究所"
    assert pages[1]["text"] == "资格要求：具备营业执照"
    assert all(page.scrolled for page in rendered_pages)


def test_different_project_codes_and_lifecycle_not_merged():
    tender = CandidateRecord(
        title="服务器、数据库、数据库集群软件招标公告",
        source_name="cebpub",
        source_url="https://example/1",
        project_code="C1100000189017141002",
        announcement_type="招标公告",
    )
    termination = CandidateRecord(
        title="服务器、数据库、数据库集群软件终止公告",
        source_name="cebpub",
        source_url="https://example/2",
        project_code="C1100000189017141001",
        announcement_type="终止公告",
    )
    assert is_duplicate(tender, termination)[0] is False


def test_purchaser_consistency_handles_spaced_pdf_label():
    content = "招 标 人 ： 西安航天动力试验技术研究所\n项目编号：TEST-001"
    result = build_extraction_data(
        title="服务器招标公告",
        clean_content=content,
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    assert result["fields"]["purchaser"] == "西安航天动力试验技术研究所"
    assert result["fields"]["purchaser_source_label"] == "招标人"
    assert result["field_records"]["purchaser"]["status"] == "verified"


def test_purchaser_label_without_reliable_value_is_review_failure():
    content = "招 标 人：\n联系方式：029-12345678"
    result = build_extraction_data(
        title="服务器招标公告",
        clean_content=content,
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    assert result["fields"]["purchaser"] == "提取失败，待复核"
    assert result["field_records"]["purchaser"]["status"] == "extraction_failed"
    assert result["quality_status"] == "needs_review"


def test_ai_source_chunks_preserve_page_markers_without_raw_html():
    chunks, truncated = _ai_source_chunks(
        "fallback",
        {
            "content_pages": [
                {"page": 1, "text": "招标人：某研究所"},
                {"page": 2, "text": "投标人资格要求：具备营业执照"},
            ]
        },
        chunk_chars=1000,
    )
    assert truncated is False
    assert "[第1页]" in chunks[0]
    assert "[第2页]" in chunks[0]
    assert "<script" not in chunks[0]
