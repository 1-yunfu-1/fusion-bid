"""公告详情、企业画像、重抽取与校正审计 API。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.announcements as announcements_api
from app.models.announcement import TenderAnnouncement
from app.reports.fields import build_extraction_data
from app.sources.base import DetailResult


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


async def _create_pending_announcement(db_engine) -> str:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = TenderAnnouncement(
            title="服务器、数据库、数据库集群软件招标公告",
            source_name="cebpub",
            source_url="https://ctbpsp.com/",
            detail_url="https://ctbpsp.com/",
            source_item_id="1d1600b68217477890a8076bc98a6880",
            data_mode="live",
            detail_status="metadata_only",
            extraction_version="needs_recrawl",
            clean_content="项目名称：服务器项目",
            raw_content="项目名称：服务器项目",
            source_metadata={},
            attachment_links=[],
            related_urls=[],
            related_sources=[],
            dedupe_reasons=[],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def test_recrawl_falls_back_to_interactive_and_extracts_purchaser(
    client, db_engine, monkeypatch
):
    announcement_id = await _create_pending_announcement(db_engine)
    content = (
        "服务器、数据库、数据库集群软件招标公告\n"
        "招标人：西安航天动力试验技术研究所\n"
        "招标代理机构：陕西铭源项目管理有限公司\n"
        "3. 投标人资格要求\n3.1 具备有效营业执照。"
    )

    class FakeSource:
        enabled = True

        def __init__(self):
            self.calls = []

        async def fetch_detail(self, item, *, interactive=False):
            self.calls.append(interactive)
            if not interactive:
                return DetailResult(
                    title=item.title,
                    source_url=item.source_url,
                    detail_url=item.source_url,
                    detail_status="needs_human_verification",
                    detail_fetched=False,
                    source_metadata={
                        "message": "需要人工验证",
                        "failure_reason": "verification_required",
                    },
                )
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                detail_url=item.source_url,
                detail_status="full",
                detail_fetched=True,
                content_format="pdf_text",
                clean_content=content,
                raw_content=content,
                source_metadata={
                    "content_pages": [{"page": 1, "text": content}],
                    "acquisition_mode": "managed_chrome",
                    "browser_reused": True,
                    "browser_state": "ready",
                },
            )

        async def extract_attachments(self, detail):
            return []

    source = FakeSource()
    monkeypatch.setattr(announcements_api, "get_source", lambda _name: source)
    response = await client.post(
        f"/api/announcements/{announcement_id}/recrawl",
        json={"interactive_on_verification": True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert source.calls == [False, True]
    assert payload["ok"] is True
    assert payload["acquisition_mode"] == "managed_chrome"
    assert payload["browser_reused"] is True
    assert payload["browser_state"] == "ready"
    assert payload["verification_attempted"] is True
    assert payload["announcement"]["fields"]["purchaser"] == "西安航天动力试验技术研究所"
    assert payload["announcement"]["fields"]["purchaser_source_label"] == "招标人"


async def test_duplicate_recrawl_returns_409(client, db_engine):
    announcement_id = await _create_pending_announcement(db_engine)
    announcements_api._active_recrawls.add(announcement_id)
    try:
        response = await client.post(f"/api/announcements/{announcement_id}/recrawl")
    finally:
        announcements_api._active_recrawls.discard(announcement_id)
    assert response.status_code == 409


async def test_default_recrawl_never_opens_interactive_browser(
    client, db_engine, monkeypatch
):
    announcement_id = await _create_pending_announcement(db_engine)

    class FakeSource:
        enabled = True

        def __init__(self):
            self.calls = []

        async def fetch_detail(self, item, *, interactive=False):
            self.calls.append(interactive)
            return DetailResult(
                title=item.title,
                source_url=item.source_url,
                detail_url=item.source_url,
                detail_status="needs_human_verification",
                detail_fetched=False,
                source_metadata={
                    "message": "官方页面要求验证",
                    "failure_reason": "verification_required",
                    "acquisition_mode": "managed_chrome",
                    "browser_state": "needs_verification",
                },
            )

        async def extract_attachments(self, detail):
            return []

    source = FakeSource()
    monkeypatch.setattr(announcements_api, "get_source", lambda _name: source)

    response = await client.post(f"/api/announcements/{announcement_id}/recrawl")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert source.calls == [False]
    assert payload["ok"] is False
    assert payload["verification_attempted"] is False
    assert payload["browser_state"] == "needs_verification"


async def test_browser_text_layer_capture_extracts_verified_pages(client, db_engine):
    await _create_pending_announcement(db_engine)
    detail_url = (
        "https://ctbpsp.com/#/bulletinDetail?"
        "uuid=1d1600b68217477890a8076bc98a6880&inpvalue=&dataSource=0"
    )
    response = await client.post(
        "/api/announcements/capture-rendered-detail",
        json={
            "source_name": "cebpub",
            "source_item_id": "1d1600b68217477890a8076bc98a6880",
            "detail_url": detail_url,
            "outer_text": "服务器、数据库、数据库集群软件招标公告 接收时间",
            "page_count": 2,
            "pages": [
                {
                    "page": 1,
                    "items": [
                        {"text": "招标人：", "x": 10, "y": 100, "width": 40},
                        {
                            "text": "西安航天动力试验技术研究所",
                            "x": 55,
                            "y": 100,
                            "width": 150,
                        },
                        {
                            "text": "3.1 具备有效营业执照。",
                            "x": 10,
                            "y": 80,
                            "width": 180,
                        },
                    ],
                },
                {
                    "page": 2,
                    "text": "招标代理机构：陕西铭源项目管理有限公司",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["acquisition_mode"] == "browser_extension"
    assert payload["page_count"] == 2
    assert payload["announcement"]["fields"]["purchaser"] == "西安航天动力试验技术研究所"
    assert payload["announcement"]["source_metadata"]["browser_capture"]["cookies_received"] is False


async def test_browser_capture_rejects_missing_page_without_mutation(client, db_engine):
    announcement_id = await _create_pending_announcement(db_engine)
    response = await client.post(
        "/api/announcements/capture-rendered-detail",
        json={
            "source_name": "cebpub",
            "source_item_id": "1d1600b68217477890a8076bc98a6880",
            "detail_url": "https://ctbpsp.com/#/bulletinDetail?uuid=1d1600b68217477890a8076bc98a6880",
            "outer_text": "服务器、数据库、数据库集群软件招标公告",
            "page_count": 2,
            "pages": [{"page": 1, "text": "只有第一页"}],
        },
    )

    assert response.status_code == 422
    detail = await client.get(f"/api/announcements/{announcement_id}")
    assert detail.json()["detail_status"] == "metadata_only"


async def test_import_official_html_extracts_and_analyzes(client, db_engine):
    announcement_id = await _create_pending_announcement(db_engine)
    html = """
    <html><body><article>
      <h1>服务器、数据库、数据库集群软件招标公告</h1>
      <p>招标人：西安航天动力试验技术研究所</p>
      <p>招标代理机构：陕西铭源项目管理有限公司</p>
      <h2>3. 投标人资格要求</h2>
      <p>3.1 具备有效营业执照。</p>
      <p>3.2 不接受联合体投标。</p>
      <h2>4. 招标文件获取</h2>
    </article><script>window.bad = true</script></body></html>
    """
    response = await client.post(
        f"/api/announcements/{announcement_id}/import-detail",
        files={"file": ("官方公告.html", html.encode(), "text/html")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["acquisition_mode"] == "manual_import"
    assert payload["content_format"] == "html"
    assert payload["announcement"]["detail_status"] == "full"
    assert (
        payload["announcement"]["fields"]["purchaser"]
        == "西安航天动力试验技术研究所"
    )
    metadata = payload["announcement"]["source_metadata"]
    assert metadata["manual_import"]["identity_basis"] == "title"
    assert metadata["manual_import"]["filename"] == "官方公告.html"
    assert "window.bad" not in payload["announcement"]["clean_content"]


async def test_import_identity_mismatch_preserves_pending_record(client, db_engine):
    announcement_id = await _create_pending_announcement(db_engine)
    response = await client.post(
        f"/api/announcements/{announcement_id}/import-detail",
        files={
            "file": (
                "其他项目.html",
                b"<html><body>unrelated official document</body></html>",
                "text/html",
            )
        },
    )

    assert response.status_code == 422
    detail = await client.get(f"/api/announcements/{announcement_id}")
    assert detail.json()["detail_status"] == "metadata_only"
