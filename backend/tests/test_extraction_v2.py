"""详情抽取 v2 与证据强校验回归。"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.browser.pdf_detail import (
    _extract_rendered_text_pages,
    _ocr_rendered_pages,
    _wait_for_pdf_frame,
    restore_reading_order,
)
from app.deduplication.engine import CandidateRecord, is_duplicate
from app.reports.fields import (
    _ai_source_chunks,
    _split_qualification_items,
    _subject_label_rank,
    _validate_ai_extraction_rows,
    build_extraction_data,
    enrich_report_item,
)


def test_joined_qualification_field_can_be_split_back_into_clauses():
    items = _split_qualification_items(
        "3.1 依法注册；3.2(‘合格来源国’)均可投标；3.10 应具备核级许可证；3.11 不得在黑名单内"
    )

    assert [item.split(maxsplit=1)[0] for item in items] == [
        "3.1",
        "3.2",
        "3.10",
        "3.11",
    ]


def test_pdf_qualification_numbers_need_no_space_before_chinese_text():
    items = _split_qualification_items(
        "3.1投标人须依法注册\n3.2本项目不允许联合体投标\n"
        "3.3本次允许代理商投标\n3.4投标人须办理CA证书"
    )

    assert [item.split(maxsplit=1)[0] for item in items] == [
        "3.1",
        "3.2",
        "3.3",
        "3.4",
    ]


def test_chinese_numbered_section_stops_qualification_before_next_chapter():
    content = """
四、投标人资格要求
1、依法注册并具有履约能力
2、本项目不接受联合体投标
五、招标文件获取方式
1、登录交易平台下载招标文件
六、投标截止时间
""".strip()

    fields = build_extraction_data(
        title="数据采集服务器采购项目招标公告",
        clean_content=content,
        detail_status="full",
    )["fields"]

    assert fields["qualification_items"] == [
        "1、 依法注册并具有履约能力",
        "2、 本项目不接受联合体投标",
    ]
    assert "招标文件获取方式" not in fields["qualification"]


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
    assert extraction["version"] == 3
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


def test_purchasing_subject_context_is_not_misclassified_as_agency():
    content = (
        '询价公示\n安徽智质工程技术有限公司（以下，简称"智质公司"）'
        "根据生产需要，拟采购云服务器租赁，现开展询比价工作。"
    )

    fields = build_extraction_data(
        title="云服务器租赁及DNS解析",
        clean_content=content,
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )["fields"]

    assert fields["purchaser"] == "安徽智质工程技术有限公司"
    assert fields["purchaser_source_label"] == "拟采购主体"
    assert fields["agency"] == "原文未明确说明"


def test_ai_agency_requires_agency_or_entrustment_semantics_in_quote():
    content = (
        '安徽智质工程技术有限公司（以下，简称"智质公司"）'
        "根据生产需要，拟采购云服务器租赁。"
    )
    rows, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "agency",
                    "value": "安徽智质工程技术有限公司",
                    "source_label": "询价公示",
                    "quote": content,
                    "page": 1,
                }
            ]
        },
        clean_content=content,
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )

    assert rows == []
    assert any("不含代理/招标机构或受托语义" in error for error in errors)


def test_ccgp_overview_names_override_generic_information_headings():
    content = """
采购人
信息
采购单位
中国医科大学附属盛京医院
采购代理机构信息
名称
代理机构名称
辽宁省公共资源交易中心(辽宁省政府采购中心)
""".strip()

    fields = build_extraction_data(
        title="服务器及存储设备项目中标公告",
        clean_content=content,
        detail_status="full",
    )["fields"]

    assert fields["purchaser"] == "中国医科大学附属盛京医院"
    assert fields["purchaser_source_label"] == "采购单位"
    assert fields["agency"] == "辽宁省公共资源交易中心(辽宁省政府采购中心)"


def test_ccgp_detail_beats_label_only_summary_and_extracts_wrapped_fields():
    content = """
公告概要：
采购项目名称
安徽省产品质量监督检验研究院服务器采购项目
采购单位
安徽省产品质量监督检验研究院
行政区域
安徽省
采购单位联系方式
/
代理机构名称
安徽省招标集团股份有限公司
二、申请人的资格要求：
1.
满足《中华人民共和国政府采购法》第二十二条规定。
2.
落实政府采购政策需满足的资格要求：无。
3.
本项目的特定资格要求：无。
三、获取招标文件
四、提交投标文件
截止时间、开标时间和地点
时间：
2026
年
8
月
7
日
10
点
00
分（北京时间）
""".strip()
    summary = """
采购项目名称
采购单位
获取招标文件的地点
采购单位
采购单位地址
采购单位联系方式
""".strip()

    fields = build_extraction_data(
        title="安徽省产品质量监督检验研究院服务器采购项目招标公告",
        summary=summary,
        clean_content=content,
        region="采购人：安徽省产品质量监督检验研究院",
        detail_status="full",
    )["fields"]

    assert fields["purchaser"] == "安徽省产品质量监督检验研究院"
    assert fields["purchaser_source_label"] == "采购单位"
    assert fields["agency"] == "安徽省招标集团股份有限公司"
    assert fields["region"] == "安徽省"
    assert fields["project_name"] == "安徽省产品质量监督检验研究院服务器采购项目"
    assert len(fields["qualification_items"]) == 3
    assert fields["bid_deadline"] == "2026年8月7日 10:00"
    assert fields["opening_time"] == "2026年8月7日 10:00"


def test_ai_entity_placeholder_is_rejected_even_with_exact_quote():
    content = "采购人\n/\n采购单位\n安徽省产品质量监督检验研究院"
    rows, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "purchaser",
                    "value": "/",
                    "source_label": "采购人",
                    "quote": "采购人\n/",
                }
            ]
        },
        clean_content=content,
        source_metadata=None,
    )

    assert rows == []
    assert any("占位符" in error for error in errors)


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


def test_ai_purchaser_requires_real_source_label():
    content = "中国原子能工业有限公司受招标人委托对下列产品进行招标"
    valid, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "purchaser",
                    "value": "委托对下列产品进行招标",
                    "source_label": "purchaser",
                    "quote": "招标人委托对下列产品进行招标",
                    "page": 1,
                }
            ]
        },
        clean_content=content,
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    assert valid == []
    assert any("source_label" in error for error in errors)


def test_subject_label_priority_prefers_tenderer_over_project_owner():
    assert _subject_label_rank("采购人") > _subject_label_rank("招标人")
    assert _subject_label_rank("采购单位") == _subject_label_rank("采购人")
    assert _subject_label_rank("招标人") > _subject_label_rank("项目业主")


def test_ai_verified_datetime_is_stored_in_normalized_form():
    content = "5.1投标截止时间为2026-08-06 14:00:00（北京时间）"
    valid, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "bid_deadline",
                    "value": "2026-08-06 14:00:00",
                    "source_label": "投标截止时间",
                    "quote": content,
                    "page": 1,
                }
            ]
        },
        clean_content=content,
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )

    assert errors == []
    assert valid[0]["value"] == "2026年8月6日 14:00"


def test_ai_verified_mixed_currency_price_drops_redundant_yuan_symbol():
    content = "招标文件售价￥：￥200/$30"
    valid, errors = _validate_ai_extraction_rows(
        {
            "fields": [
                {
                    "name": "document_price",
                    "value": "￥200/$30",
                    "source_label": "招标文件售价￥",
                    "quote": content,
                    "page": 1,
                }
            ]
        },
        clean_content=content,
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )

    assert errors == []
    assert valid[0]["value"] == "200/$30"


def test_international_pdf_reversed_cells_extract_complete_fields():
    content = """
【第1页】
澄清或变更简要说明：修改投标截止时间
2026-07-17
中国原子能工业有限公司受招标人委托对下列产品及服务进行国际公开竞争性招标。
:0739-264CNEIC2M09
招标项目编号
3
、投标人资格要求
:3.1
投标人是响应招标并参加投标竞争的法人或其他组织。
3.2
来自合格来源国或地区的法人或其他组织均可投标。
3.7
投标人须在中国国际招标网成功注册并核验。
3.8
本次招标不接受联合体投标。
3.9
本次招标不接受代理商投标。
3.10
核级稳压器安全阀制造商应取得相应设计及制造许可证。
3.11
投标人不得处于供应商黑名单有效期内。
4
、招标文件的获取
:2026-05-09
招标文件领购开始时间
:2026-05-15
招标文件领购结束时间
:200/$30
招标文件售价￥
5
、投标文件的递交
:2026-08-11 09:30
投标截止时间（开标时间）
6
、联系方式
:
招标人中国核电工程有限公司
:
招标代理机构中国原子能工业有限公司
【第2页】
联系人：吴女士
""".strip()
    extraction = build_extraction_data(
        title="华能霞浦核电项目安全阀设备国际招标澄清或变更公告(9)",
        clean_content=content,
        project_code="0739-264CNEIC2M09000",
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    fields = extraction["fields"]
    assert fields["tenderer"] == "中国核电工程有限公司"
    assert fields["purchaser"] == "中国核电工程有限公司"
    assert fields["purchaser_source_label"] == "招标人"
    assert fields["tenderer_source_label"] == "招标人"
    assert fields["agency"] == "中国原子能工业有限公司"
    assert fields["project_code"] == "0739-264CNEIC2M09"
    assert fields["bid_deadline"] == "2026年8月11日 09:30"
    assert fields["opening_time"] == "2026年8月11日 09:30"
    assert fields["document_acquisition_start"] == "2026年5月9日"
    assert fields["document_acquisition_end"] == "2026年5月15日"
    assert fields["document_price"] == "200/$30"
    assert fields["platform_registration_required"] == "需要"
    assert len(fields["qualification_items"]) == 7
    assert fields["qualification_items"][0].startswith("3.1")
    assert fields["qualification_items"][-1].startswith("3.11")


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


@pytest.mark.asyncio
async def test_pdf_frame_waits_for_frame_attachment(monkeypatch):
    frame = type("Frame", (), {"url": "https://ctbpsp.com/web_pdf/viewer.html"})()

    class DelayedPage:
        reads = 0

        @property
        def frames(self):
            self.reads += 1
            return [] if self.reads < 3 else [frame]

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr("app.browser.pdf_detail.asyncio.sleep", fast_sleep)
    page = DelayedPage()

    assert await _wait_for_pdf_frame(page, timeout_ms=1000) is frame
    assert page.reads == 3


def test_raw_pdf_section_prefixes_and_table_layout_preserve_dates_and_price():
    content = """
2.招标内容、交货期、交货地点及招标文件售价：
计量单位 交货期 招标文件售价人民币 备注
标段（包）编号 货物名称 数量 交货地点
位 期 （元） 注
C1100000189017141002001 服务器、数据库、数据库集群软件 1.000 套 1周 800.0
3.投标人资格要求
3.1投标人须依法注册。
4.招标文件的获取
4.1参与：凡有意参加投标者，请于2026年07月16日22时00分00秒至2026年07月23日22时00分00秒下载招标文件。
5.投标文件递交及开标信息
5.1投标截止时间为2026-08-06 14:00:00（北京时间）。
5.3开标时间为2026年08月06日14时00分（北京时间）。
""".strip()

    extraction = build_extraction_data(
        title="服务器、数据库、数据库集群软件招标公告",
        clean_content=content,
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    fields = extraction["fields"]

    assert fields["document_price"] == "800元"
    assert fields["document_acquisition_start"] == "2026年7月16日 22:00"
    assert fields["document_acquisition_end"] == "2026年7月23日 22:00"
    assert fields["bid_deadline"] == "2026年8月6日 14:00"
    assert fields["opening_time"] == "2026年8月6日 14:00"


def test_real_style_numbered_deadline_agency_and_started_range_are_extracted():
    content = """
五、招标文件获取方式
1、有意向的潜在投标人，可自2026年7月16日起至2026年7月22日23:59止（北京时间），登录平台获取招标文件。
六、投标截止时间、文件递交地点
1、投标截止时间：2026年7月29日15时00分（北京时间）
八、联系方式
2、招标机构：安徽省皖北煤电集团有限责任公司招标中心（以下简称“招标中心”）
招标人：安徽恒源煤电股份有限公司供应分公司
""".strip()

    fields = build_extraction_data(
        title="数据采集服务器采购项目招标公告",
        clean_content=content,
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )["fields"]

    assert fields["purchaser"] == "安徽恒源煤电股份有限公司供应分公司"
    assert fields["agency"] == "安徽省皖北煤电集团有限责任公司招标中心"
    assert fields["document_acquisition_start"] == "2026年7月16日"
    assert fields["document_acquisition_end"] == "2026年7月22日 23:59"
    assert fields["bid_deadline"] == "2026年7月29日 15:00"


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


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_only_processes_missing_pages(monkeypatch):
    fake_rapidocr = ModuleType("rapidocr")
    fake_rapidocr.RapidOCR = object
    calls: list[bytes] = []

    def recognise(image_bytes: bytes):
        calls.append(image_bytes)
        return "第2页扫描正文：招标人 某研究所"

    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapidocr)
    monkeypatch.setattr("app.browser.pdf_detail._recognise_image_bytes", recognise)

    class ScannedPage:
        def __init__(self, number: int) -> None:
            self.number = number
            self.scrolled = False

        async def scroll_into_view_if_needed(self, **_kwargs):
            self.scrolled = True

        async def screenshot(self, **_kwargs):
            return f"page-{self.number}".encode()

    scanned_pages = [ScannedPage(1), ScannedPage(2), ScannedPage(3)]

    pages = await _ocr_rendered_pages(
        _FakeFrame(scanned_pages), page_numbers={2}
    )

    assert pages == [
        {
            "page": 2,
            "text": "第2页扫描正文：招标人 某研究所",
            "method": "ocr",
        }
    ]
    assert scanned_pages[0].scrolled is False
    assert scanned_pages[1].scrolled is True
    assert scanned_pages[2].scrolled is False
    assert calls == [b"page-2"]


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
