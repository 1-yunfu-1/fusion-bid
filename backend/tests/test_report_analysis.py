"""Rule-first decision analysis remains useful when no LLM is configured."""

from __future__ import annotations

import pytest

from app.reports.analysis import build_execution_analysis


@pytest.mark.asyncio
async def test_rule_analysis_marks_metadata_only_as_needing_verification(monkeypatch):
    async def unavailable(*args, **kwargs):
        class Result:
            success = False

        return Result()

    monkeypatch.setattr("app.reports.analysis.call_json_llm_chain", unavailable)
    analysis = await build_execution_analysis(
        [
            {
                "announcement_id": "a1",
                "title": "福建核电项目招标公告",
                "source_name": "cebpub",
                "source_url": "https://example.test/a1",
                "clean_content": "项目名称：福建核电项目招标公告\n招标人名称：福建某核电有限公司",
                "detail_status": "metadata_only",
                "detail_fetched": False,
            }
        ],
        keywords=["核电"],
        regions=["福建省"],
        start_date="2026-07-01",
        end_date="2026-07-31",
    )
    assert analysis["status"] == "rule_only"
    project = analysis["projects"][0]
    assert project["priority"] == "待核验"
    assert any("可验证的公告详情" in gap for gap in project["gaps"])


@pytest.mark.asyncio
async def test_llm_note_without_evidence_is_rejected(monkeypatch):
    class Result:
        success = True
        provider = "api"
        model = "test"
        data = {
            "portfolio_summary": "存在一个可关注项目。",
            "project_notes": [
                {
                    "announcement_id": "a1",
                    "analysis": "建议立刻投标，预算充足。",
                    "evidence_fields": ["budget"],
                }
            ],
        }

    async def fake_llm(*args, **kwargs):
        return Result()

    monkeypatch.setattr("app.reports.analysis.call_json_llm_chain", fake_llm)
    analysis = await build_execution_analysis(
        [
            {
                "announcement_id": "a1",
                "title": "福建核电项目招标公告",
                "source_name": "cebpub",
                "source_url": "https://example.test/a1",
                "clean_content": "招标人名称：福建某核电有限公司\n投标人资格要求：具备有效营业执照",
                "detail_status": "full",
                "detail_fetched": True,
            }
        ],
        keywords=["核电"],
        regions=["福建省"],
        start_date=None,
        end_date=None,
    )
    assert analysis["status"] == "rule_only"
    assert "llm_note" not in analysis["projects"][0]
