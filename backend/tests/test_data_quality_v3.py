"""生命周期语义、金额隔离和类型化完整度回归。"""

from __future__ import annotations

from datetime import date

from app.deduplication.engine import CandidateRecord, is_duplicate
from app.reports.analysis import _project_rule
from app.reports.fields import build_extraction_data, data_completeness
from app.reports.lifecycle import (
    classify_announcement,
    classify_lifecycle,
)


def test_lifecycle_title_has_priority_over_procurement_method_in_body() -> None:
    cases = {
        "某项目中标(成交)结果公告": "结果公告",
        "某项目成交公告": "结果公告",
        "某项目流标公示": "终止/废标",
        "某项目澄清或变更公告": "更正/澄清",
    }
    for title, expected in cases.items():
        stage, method = classify_announcement(
            title=title,
            content="本项目原采购方式为单一来源采购。",
        )
        assert stage == expected
        assert method == "单一来源"


def test_result_amount_is_not_budget_and_has_no_participation_advice() -> None:
    extraction = build_extraction_data(
        title="服务器采购项目中标(成交)结果公告",
        clean_content=(
            "采购人：测试采购中心\n"
            "项目编号：RESULT-2026-001\n"
            "成交供应商名称：测试科技有限公司\n"
            "总中标金额：407.66万元\n"
            "采购方式：单一来源"
        ),
        detail_status="full",
    )
    fields = extraction["fields"]
    assert fields["lifecycle_stage"] == "结果公告"
    assert fields["procurement_method"] == "单一来源"
    assert fields["awardee"] == "测试科技有限公司"
    assert "407.66" in fields["award_amount"]
    assert fields["budget"] == "原文未明确说明"

    project = _project_rule(
        {
            "announcement_id": "result-1",
            "title": "服务器采购项目中标(成交)结果公告",
            "fields": fields,
            "field_evidence": extraction["evidence"],
            "detail_status": "full",
        },
        today=date(2026, 7, 19),
    )
    assert project["is_opportunity"] is False
    assert project["decision"] == "不适用（生命周期情报）"
    assert "参与" not in "；".join(project["recommended_actions"])


def test_result_table_header_is_not_mistaken_for_awardee() -> None:
    extraction = build_extraction_data(
        title="云服务器服务中标(成交)结果公告",
        clean_content=(
            "采购人：浙江工业大学\n"
            "项目编号：RESULT-2026-HEADER\n"
            "中标供应商名称 中标供应商地址 中标（成交）金额\n"
            "中标供应商名称：浙江云计算科技有限公司\n"
            "中标供应商地址：杭州市西湖区\n"
            "中标（成交）金额：59.95万元"
        ),
        detail_status="full",
    )

    fields = extraction["fields"]
    assert fields["awardee"] == "浙江云计算科技有限公司"
    assert fields["awardee"] != "中标供应商地址"


def test_flattened_result_table_maps_supplier_column_to_first_row() -> None:
    extraction = build_extraction_data(
        title="云服务器服务中标(成交)结果公告",
        clean_content=(
            "三、中标（成交）信息\n"
            "1.中标结果：\n"
            "序号\n"
            "中标（成交）金额(元)\n"
            "中标供应商名称\n"
            "中标供应商地址\n"
            "评审总得分\n"
            "1\n"
            "总价：599500（元）\n"
            "陕西天翊瑞通信息科技有限公司\n"
            "陕西省西咸新区泾河新城\n"
            "77.49"
        ),
        detail_status="full",
    )

    assert extraction["fields"]["awardee"] == "陕西天翊瑞通信息科技有限公司"


def test_completeness_uses_lifecycle_specific_fields() -> None:
    result = data_completeness(
        {
            "lifecycle_stage": "结果公告",
            "purchaser": "采购中心",
            "project_code": "P-1",
            "awardee": "供应商",
            "award_amount": "100万元",
        },
        has_attachments=False,
    )
    assert result["percent"] == 100
    assert result["required_fields"] == [
        "purchaser",
        "project_code",
        "awardee",
        "award_amount",
    ]


def test_different_lifecycle_nodes_with_same_project_code_do_not_merge() -> None:
    opportunity = CandidateRecord(
        title="服务器项目公开招标公告",
        source_name="ccgp",
        source_url="https://example.test/opportunity",
        project_code="P-2026-1",
        lifecycle_stage="机会公告",
    )
    result = CandidateRecord(
        title="服务器项目成交公告",
        source_name="ccgp",
        source_url="https://example.test/result",
        project_code="P-2026-1",
        lifecycle_stage="结果公告",
    )
    assert is_duplicate(opportunity, result) == (False, "")


def test_unknown_title_is_marked_for_review() -> None:
    assert classify_lifecycle("关于服务器项目的通知") == "待复核"
