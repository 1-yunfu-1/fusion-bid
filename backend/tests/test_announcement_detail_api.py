"""公告详情、企业画像、重抽取与校正审计 API。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.announcement import TenderAnnouncement
from app.reports.fields import build_extraction_data


async def _create_announcement(db_engine) -> str:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    content = (
        "服务器项目招标公告\n"
        "招标人：测试研究所\n"
        "招标代理机构：测试项目管理有限公司\n"
        "3. 投标人资格要求\n"
        "3.1 具备有效营业执照。\n"
        "4. 招标文件获取\n"
        "投标截止时间：2099年8月6日14时00分\n"
    )
    extraction = build_extraction_data(
        title="服务器项目招标公告",
        clean_content=content,
        project_code="TEST-2099-001",
        detail_status="full",
        source_metadata={"content_pages": [{"page": 1, "text": content}]},
    )
    async with factory() as db:
        row = TenderAnnouncement(
            title="服务器项目招标公告",
            source_name="cebpub",
            source_url="https://ctbpsp.com/#/bulletinDetail?uuid=test",
            detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=test",
            source_item_id="test",
            data_mode="live",
            detail_status="full",
            content_format="pdf_text",
            extraction_version="v2",
            clean_content=content,
            raw_content=content,
            extraction_data=extraction,
            source_metadata={"content_pages": [{"page": 1, "text": content}]},
            project_code="TEST-2099-001",
            announcement_type="招标公告",
            attachment_links=[],
            related_urls=[],
            related_sources=[],
            dedupe_reasons=[],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def test_detail_reextract_analyze_and_manual_correction(client, db_engine):
    announcement_id = await _create_announcement(db_engine)

    detail = await client.get(f"/api/announcements/{announcement_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["detail_status"] == "full"
    assert payload["fields"]["purchaser"] == "测试研究所"
    assert payload["field_evidence"]["purchaser"]["page"] == 1

    reextract = await client.post(f"/api/announcements/{announcement_id}/reextract")
    assert reextract.status_code == 200, reextract.text
    assert reextract.json()["announcement"]["extraction_version"] == "v2"

    corrected = await client.patch(
        f"/api/announcements/{announcement_id}/fields",
        json={"fields": {"purchaser": "校正后招标人"}, "reason": "人工核对官方原文"},
    )
    assert corrected.status_code == 200, corrected.text
    corrected_payload = corrected.json()["announcement"]
    assert corrected_payload["fields"]["purchaser"] == "校正后招标人"
    assert corrected_payload["corrections"][0]["reason"] == "人工核对官方原文"

    reextract_after_correction = await client.post(
        f"/api/announcements/{announcement_id}/reextract"
    )
    assert reextract_after_correction.status_code == 200
    assert (
        reextract_after_correction.json()["announcement"]["fields"]["purchaser"]
        == "校正后招标人"
    )

    analyzed = await client.post(f"/api/announcements/{announcement_id}/analyze")
    assert analyzed.status_code == 200
    assert analyzed.json()["analysis"]["decision"] in {
        "建议参与",
        "有条件参与",
        "不建议参与",
        "信息不足",
    }


async def test_company_profile_roundtrip(client):
    empty = await client.get("/api/company-profile")
    assert empty.status_code == 200
    assert empty.json()["configured"] is False

    saved = await client.put(
        "/api/company-profile",
        json={
            "name": "测试企业",
            "product_capabilities": ["服务器"],
            "service_regions": ["陕西省"],
            "qualifications": ["营业执照（有效期内）"],
            "cases": ["某服务器项目"],
            "delivery_constraints": [],
            "agent_capability": True,
            "joint_venture_capability": False,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["configured"] is True
    loaded = await client.get("/api/company-profile")
    assert loaded.json()["name"] == "测试企业"
    assert loaded.json()["product_capabilities"] == ["服务器"]
