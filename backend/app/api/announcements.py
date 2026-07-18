"""公告查询、详情重采、重抽取、分析与人工校正 API。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.browser.pdf_detail import restore_reading_order
from app.importers.official_document import (
    MAX_UPLOAD_BYTES,
    OfficialDocumentError,
    extract_official_document,
)
from app.models.announcement import TenderAnnouncement
from app.models.company import AnnouncementFieldCorrection, CompanyProfile
from app.models.delivery import DeliveryHistory
from app.reports.analysis import build_execution_analysis
from app.reports.fields import (
    apply_manual_corrections,
    build_extraction_data_with_ai,
    enrich_report_item,
)
from app.sources.base import ListItem
from app.sources.registry import get_source

router = APIRouter(prefix="/announcements", tags=["announcements"])
TZ = ZoneInfo("Asia/Shanghai")

_CORRECTABLE_FIELDS = {
    "purchaser",
    "purchaser_source_label",
    "tenderer",
    "tenderer_source_label",
    "agency",
    "transaction_platform",
    "project_code",
    "budget",
    "document_price",
    "funding_source",
    "notice_end_time",
    "document_acquisition_start",
    "document_acquisition_end",
    "bid_deadline",
    "deadline",
    "opening_time",
    "region",
    "content",
    "announcement_type",
    "qualification",
    "qualification_items",
    "joint_venture_allowed",
    "agent_allowed",
    "platform_registration_required",
    "ca_required",
}


class FieldCorrectionRequest(BaseModel):
    fields: dict[str, Any]
    reason: str = Field(min_length=2, max_length=1000)


class RecrawlRequest(BaseModel):
    interactive_on_verification: bool = Field(
        default=True,
        description="手工重采遇到官方验证时是否启动可见浏览器",
    )


class RenderedTextItem(BaseModel):
    text: str = Field(max_length=20_000)
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


class RenderedCapturePage(BaseModel):
    page: int = Field(ge=1, le=100)
    text: str = Field(default="", max_length=200_000)
    items: list[RenderedTextItem] = Field(default_factory=list, max_length=20_000)


class RenderedDetailCaptureRequest(BaseModel):
    source_name: Literal["cebpub"] = "cebpub"
    source_item_id: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    detail_url: str = Field(min_length=20, max_length=2_000)
    outer_text: str = Field(min_length=1, max_length=200_000)
    page_count: int = Field(ge=1, le=100)
    pages: list[RenderedCapturePage] = Field(min_length=1, max_length=100)


_recrawl_guard = asyncio.Lock()
_active_recrawls: set[str] = set()


async def _claim_recrawl(announcement_id: str) -> bool:
    async with _recrawl_guard:
        if announcement_id in _active_recrawls:
            return False
        _active_recrawls.add(announcement_id)
        return True


async def _release_recrawl(announcement_id: str) -> None:
    async with _recrawl_guard:
        _active_recrawls.discard(announcement_id)


def _content_hash(announcement: TenderAnnouncement) -> str:
    value = (
        f"{announcement.title}|{(announcement.clean_content or '')[:2000]}|"
        f"{announcement.source_url}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise_capture_identity(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


def _base_item(announcement: TenderAnnouncement) -> dict[str, Any]:
    return {
        "announcement_id": announcement.id,
        "id": announcement.id,
        "title": announcement.title,
        "source_name": announcement.source_name,
        "source_url": announcement.source_url,
        "detail_url": announcement.detail_url or announcement.source_url,
        "source_item_id": announcement.source_item_id,
        "data_mode": announcement.data_mode,
        "requires_login": announcement.requires_login,
        "publish_time": (
            announcement.publish_time.isoformat() if announcement.publish_time else None
        ),
        "region": announcement.region,
        "province": announcement.province,
        "city": announcement.city,
        "keywords": announcement.keywords or [],
        "summary": announcement.summary,
        "clean_content": announcement.clean_content or "",
        "attachment_links": announcement.attachment_links or [],
        "crawl_time": (
            announcement.crawl_time.isoformat() if announcement.crawl_time else None
        ),
        "content_hash": announcement.content_hash,
        "deduplication_key": announcement.deduplication_key,
        "detail_status": announcement.detail_status,
        "content_format": announcement.content_format,
        "extraction_version": announcement.extraction_version,
        "announcement_type": announcement.announcement_type,
        "source_metadata": announcement.source_metadata or {},
        "extraction_data": announcement.extraction_data or {},
        "analysis_data": announcement.analysis_data or {},
        "project_code": announcement.project_code,
        "is_primary": announcement.is_primary,
        "related_urls": announcement.related_urls or [announcement.source_url],
        "related_sources": announcement.related_sources or [],
        "dedupe_reasons": announcement.dedupe_reasons or [],
        "detail_fetched": announcement.detail_status == "full",
    }


async def _analysis_for(
    announcement: TenderAnnouncement, db: AsyncSession
) -> dict[str, Any]:
    profile = await db.scalar(select(CompanyProfile).order_by(CompanyProfile.updated_at.desc()))
    analysis = await build_execution_analysis(
        [_base_item(announcement)],
        keywords=list(announcement.keywords or []),
        regions=[announcement.region] if announcement.region else [],
        start_date=None,
        end_date=None,
        company_profile=profile.profile_data if profile else None,
    )
    project = next(
        (row for row in analysis.get("projects", []) if isinstance(row, dict)), {}
    )
    announcement.analysis_data = project
    return project


async def _manual_corrections_for(
    announcement_id: str, db: AsyncSession
) -> list[AnnouncementFieldCorrection]:
    return list(
        (
            await db.execute(
                select(AnnouncementFieldCorrection)
                .where(AnnouncementFieldCorrection.announcement_id == announcement_id)
                .order_by(AnnouncementFieldCorrection.corrected_at.asc())
            )
        ).scalars().all()
    )


async def _expanded_detail(
    announcement: TenderAnnouncement, db: AsyncSession
) -> dict[str, Any]:
    item = _base_item(announcement)
    field_records = (announcement.extraction_data or {}).get("field_records") or {}
    needs_review_fields = [
        field_name
        for field_name, record in field_records.items()
        if isinstance(record, dict) and record.get("status") == "extraction_failed"
    ]
    enriched = enrich_report_item(
        item,
        keywords=list(announcement.keywords or []),
        regions=[announcement.region] if announcement.region else [],
        start_date=None,
        end_date=None,
    )
    corrections = (
        await db.execute(
            select(AnnouncementFieldCorrection)
            .where(AnnouncementFieldCorrection.announcement_id == announcement.id)
            .order_by(AnnouncementFieldCorrection.corrected_at.desc())
        )
    ).scalars().all()
    item.update(
        {
            "fields": enriched.get("fields") or {},
            "field_evidence": enriched.get("field_evidence") or {},
            "completeness": enriched.get("completeness") or {},
            "data_quality": {
                "detail_status": announcement.detail_status,
                "content_format": announcement.content_format,
                "extraction_version": announcement.extraction_version,
                "assessable": (enriched.get("completeness") or {}).get(
                    "assessable", False
                ),
                "evidence_field_count": len(enriched.get("field_evidence") or {}),
                "needs_review_fields": needs_review_fields,
            },
            "corrections": [
                {
                    "id": correction.id,
                    "field_name": correction.field_name,
                    "previous_value": correction.previous_value,
                    "corrected_value": correction.corrected_value,
                    "reason": correction.reason,
                    "corrected_at": correction.corrected_at.isoformat(),
                }
                for correction in corrections
            ],
        }
    )
    return item


@router.get("")
async def list_announcements(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_name: str | None = None,
    data_mode: str | None = None,
    task_id: str | None = None,
    detail_status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(TenderAnnouncement).order_by(TenderAnnouncement.created_at.desc())
    count_stmt = select(func.count()).select_from(TenderAnnouncement)
    filters = []
    if source_name:
        filters.append(TenderAnnouncement.source_name == source_name)
    if data_mode:
        filters.append(TenderAnnouncement.data_mode == data_mode)
    if detail_status:
        filters.append(TenderAnnouncement.detail_status == detail_status)
    if task_id:
        delivered_ids = select(DeliveryHistory.announcement_id).where(
            DeliveryHistory.task_id == task_id
        )
        filters.append(TenderAnnouncement.id.in_(delivered_ids))
    for condition in filters:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = await db.scalar(count_stmt) or 0
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return {
        "items": [
            {
                key: value
                for key, value in _base_item(row).items()
                if key not in {"clean_content", "source_metadata", "extraction_data"}
            }
            for row in rows
        ],
        "total": int(total),
    }


@router.post("/capture-rendered-detail")
async def capture_rendered_detail(
    body: RenderedDetailCaptureRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """接收用户浏览器中已获准渲染的 PDF.js 文字层。

    此接口不接收 Cookie、storage state 或 PDF 二进制，只保存逐页文字和
    坐标。页面来源、UUID、外层标题和页码完整性均通过后才进入抽取链路。
    """
    parsed = urlparse(body.detail_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "ctbpsp.com":
        raise HTTPException(status_code=422, detail="仅接受 ctbpsp.com 官方详情页")
    match = re.search(r"(?:[?&])uuid=([0-9a-fA-F]{32})(?:&|$)", body.detail_url)
    if not match or match.group(1).lower() != body.source_item_id.lower():
        raise HTTPException(status_code=422, detail="官方详情页 UUID 与采集记录不一致")

    announcement = await db.scalar(
        select(TenderAnnouncement)
        .where(
            TenderAnnouncement.source_name == body.source_name,
            TenderAnnouncement.source_item_id == body.source_item_id,
        )
        .order_by(TenderAnnouncement.updated_at.desc())
    )
    if not announcement:
        raise HTTPException(status_code=404, detail="FusionBid 中没有该官方公告记录")
    if not await _claim_recrawl(announcement.id):
        raise HTTPException(status_code=409, detail="该公告正在处理详情，请勿重复提交")

    try:
        if _normalise_capture_identity(announcement.title) not in _normalise_capture_identity(
            body.outer_text
        ):
            raise HTTPException(
                status_code=422,
                detail="官方页面标题与 FusionBid 公告不一致，已拒绝导入",
            )
        page_numbers = [row.page for row in body.pages]
        expected_numbers = list(range(1, body.page_count + 1))
        if sorted(page_numbers) != expected_numbers or len(set(page_numbers)) != len(
            page_numbers
        ):
            raise HTTPException(
                status_code=422,
                detail="PDF 页码不完整或重复，已拒绝把部分正文标记为完整详情",
            )

        pages: list[dict[str, Any]] = []
        for row in sorted(body.pages, key=lambda value: value.page):
            items = [item.model_dump() for item in row.items]
            text = restore_reading_order(items) if items else row.text.strip()
            if not text:
                raise HTTPException(
                    status_code=422,
                    detail=f"PDF 第 {row.page} 页没有可用文字",
                )
            pages.append(
                {"page": row.page, "text": text, "method": "browser_text_layer"}
            )

        clean_content = "\n".join(
            f"【第{row['page']}页】\n{row['text']}" for row in pages
        )
        captured_at = datetime.now(TZ)
        metadata = dict(announcement.source_metadata or {})
        metadata.update(
            {
                "detail_status": "full",
                "detail_fetched": True,
                "detail_url": body.detail_url,
                "content_format": "pdf_text",
                "content_pages": pages,
                "acquisition_mode": "browser_extension",
                "message": f"已从常用浏览器读取 {len(pages)} 页 PDF.js 文字层",
                "browser_capture": {
                    "captured_at": captured_at.isoformat(),
                    "page_count": body.page_count,
                    "identity_basis": "official_origin+uuid+page_title",
                    "cookies_received": False,
                    "storage_state_received": False,
                },
            }
        )
        announcement.source_url = body.detail_url
        announcement.detail_url = body.detail_url
        announcement.clean_content = clean_content
        announcement.raw_content = clean_content
        announcement.detail_status = "full"
        announcement.content_format = "pdf_text"
        announcement.source_metadata = metadata
        announcement.crawl_time = captured_at
        announcement.extraction_data = await build_extraction_data_with_ai(
            title=announcement.title,
            clean_content=clean_content,
            summary=announcement.summary or "",
            region=announcement.region,
            project_code=announcement.project_code,
            publish_time=announcement.publish_time,
            detail_status="full",
            source_metadata=metadata,
        )
        announcement.extraction_data = apply_manual_corrections(
            announcement.extraction_data,
            await _manual_corrections_for(announcement.id, db),
        )
        announcement.extraction_version = "v2"
        fields = (announcement.extraction_data or {}).get("fields") or {}
        announcement.project_code = fields.get("project_code") or announcement.project_code
        announcement.announcement_type = fields.get("announcement_type")
        announcement.content_hash = _content_hash(announcement)
        await _analysis_for(announcement, db)
        await db.commit()
        await db.refresh(announcement)
        return {
            "ok": True,
            "message": f"已从浏览器采集 {len(pages)} 页正文并完成抽取分析",
            "acquisition_mode": "browser_extension",
            "page_count": len(pages),
            "extraction_version": announcement.extraction_version,
            "announcement": await _expanded_detail(announcement, db),
        }
    finally:
        await _release_recrawl(announcement.id)


@router.get("/{announcement_id}")
async def get_announcement(
    announcement_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    announcement = await db.get(TenderAnnouncement, announcement_id)
    if not announcement:
        raise HTTPException(status_code=404, detail="公告不存在")
    return await _expanded_detail(announcement, db)


@router.post("/{announcement_id}/reextract")
async def reextract_announcement(
    announcement_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    announcement = await db.get(TenderAnnouncement, announcement_id)
    if not announcement:
        raise HTTPException(status_code=404, detail="公告不存在")
    if announcement.detail_status != "full" or not (announcement.clean_content or "").strip():
        raise HTTPException(
            status_code=409,
            detail="当前没有已验证的详情正文，请先使用「重新采集并解析」",
        )
    announcement.extraction_data = await build_extraction_data_with_ai(
        title=announcement.title,
        clean_content=announcement.clean_content or "",
        summary=announcement.summary or "",
        region=announcement.region,
        project_code=announcement.project_code,
        publish_time=announcement.publish_time,
        detail_status=announcement.detail_status,
        source_metadata=announcement.source_metadata or {},
    )
    announcement.extraction_data = apply_manual_corrections(
        announcement.extraction_data,
        await _manual_corrections_for(announcement.id, db),
    )
    announcement.extraction_version = "v2"
    fields = (announcement.extraction_data or {}).get("fields") or {}
    announcement.project_code = fields.get("project_code") or announcement.project_code
    announcement.announcement_type = fields.get("announcement_type")
    await _analysis_for(announcement, db)
    await db.commit()
    await db.refresh(announcement)
    return {
        "ok": True,
        "message": "已使用保存的公告正文重新抽取并分析",
        "announcement": await _expanded_detail(announcement, db),
    }


@router.post("/{announcement_id}/import-detail")
async def import_announcement_detail(
    announcement_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not await _claim_recrawl(announcement_id):
        raise HTTPException(status_code=409, detail="该公告正在处理详情，请勿重复提交")
    try:
        announcement = await db.get(TenderAnnouncement, announcement_id)
        if not announcement:
            raise HTTPException(status_code=404, detail="公告不存在")
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            imported = await asyncio.to_thread(
                extract_official_document,
                filename=file.filename,
                content_type=file.content_type,
                data=data,
                expected_title=announcement.title,
                expected_project_code=announcement.project_code,
            )
        except OfficialDocumentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        finally:
            await file.close()

        imported_at = datetime.now(TZ)
        metadata = dict(announcement.source_metadata or {})
        metadata.update(
            {
                "detail_status": "full",
                "detail_fetched": True,
                "content_format": imported.content_format,
                "content_pages": imported.pages,
                "acquisition_mode": "manual_import",
                "message": f"已导入官方文件并读取 {len(imported.pages)} 页正文",
                "manual_import": {
                    "filename": imported.filename,
                    "content_type": imported.content_type,
                    "size_bytes": imported.size_bytes,
                    "sha256": imported.sha256,
                    "identity_basis": imported.identity_basis,
                    "imported_at": imported_at.isoformat(),
                },
            }
        )
        announcement.clean_content = imported.clean_content
        announcement.raw_content = imported.clean_content
        announcement.detail_status = "full"
        announcement.content_format = imported.content_format
        announcement.source_metadata = metadata
        announcement.crawl_time = imported_at
        announcement.extraction_data = await build_extraction_data_with_ai(
            title=announcement.title,
            clean_content=imported.clean_content,
            summary=announcement.summary or "",
            region=announcement.region,
            project_code=announcement.project_code,
            publish_time=announcement.publish_time,
            detail_status="full",
            source_metadata=metadata,
        )
        announcement.extraction_data = apply_manual_corrections(
            announcement.extraction_data,
            await _manual_corrections_for(announcement.id, db),
        )
        announcement.extraction_version = "v2"
        fields = (announcement.extraction_data or {}).get("fields") or {}
        announcement.project_code = fields.get("project_code") or announcement.project_code
        announcement.announcement_type = fields.get("announcement_type")
        announcement.content_hash = _content_hash(announcement)
        await _analysis_for(announcement, db)
        await db.commit()
        await db.refresh(announcement)
        return {
            "ok": True,
            "message": "官方文件已导入、抽取并分析",
            "acquisition_mode": "manual_import",
            "content_format": imported.content_format,
            "page_count": len(imported.pages),
            "extraction_version": announcement.extraction_version,
            "announcement": await _expanded_detail(announcement, db),
        }
    finally:
        await _release_recrawl(announcement_id)


@router.post("/{announcement_id}/recrawl")
async def recrawl_announcement(
    announcement_id: str,
    body: RecrawlRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    body = body or RecrawlRequest()
    if not await _claim_recrawl(announcement_id):
        raise HTTPException(status_code=409, detail="该公告正在重新采集，请勿重复提交")
    try:
        return await _recrawl_announcement(announcement_id, body, db)
    finally:
        await _release_recrawl(announcement_id)


async def _recrawl_announcement(
    announcement_id: str,
    body: RecrawlRequest,
    db: AsyncSession,
) -> dict:
    announcement = await db.get(TenderAnnouncement, announcement_id)
    if not announcement:
        raise HTTPException(status_code=404, detail="公告不存在")
    source = get_source(announcement.source_name)
    if source is None or not source.enabled:
        raise HTTPException(status_code=409, detail="当前数据源不可用，无法重新采集")
    raw = dict(announcement.source_metadata or {})
    raw.update(
        {
            "businessId": announcement.source_item_id,
            "tenderProjectCode": announcement.project_code,
        }
    )
    item = ListItem(
        title=announcement.title,
        source_url=announcement.detail_url or announcement.source_url,
        source_item_id=announcement.source_item_id,
        publish_time=announcement.publish_time,
        region=announcement.region,
        raw=raw,
    )
    verification_attempted = False
    acquisition_mode = "headless"
    try:
        use_interactive_first = (
            announcement.source_name == "cebpub"
            and body.interactive_on_verification
            and announcement.detail_status == "needs_human_verification"
        )
        if use_interactive_first:
            verification_attempted = True
            acquisition_mode = "interactive"
            detail = await source.fetch_detail(item, interactive=True)
        else:
            detail = await source.fetch_detail(item, interactive=False)
            if (
                announcement.source_name == "cebpub"
                and body.interactive_on_verification
                and detail.detail_status in {"needs_human_verification", "metadata_only"}
            ):
                verification_attempted = True
                acquisition_mode = "interactive"
                detail = await source.fetch_detail(item, interactive=True)
        detail.attachment_links = await source.extract_attachments(detail)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重新采集失败: {exc}") from exc

    previous_full = announcement.detail_status == "full"
    if detail.detail_status == "full":
        announcement.source_url = detail.source_url
        announcement.detail_url = detail.detail_url or detail.source_url
        announcement.content_format = detail.content_format
        announcement.clean_content = detail.clean_content
        announcement.raw_content = detail.raw_content
        announcement.detail_status = "full"
        announcement.source_metadata = detail.source_metadata
        announcement.attachment_links = detail.attachment_links or []
        announcement.crawl_time = datetime.now(TZ)
        announcement.extraction_data = await build_extraction_data_with_ai(
            title=detail.title,
            clean_content=detail.clean_content,
            summary=announcement.summary or "",
            region=detail.region or announcement.region,
            project_code=announcement.project_code,
            publish_time=detail.publish_time or announcement.publish_time,
            detail_status="full",
            source_metadata=detail.source_metadata,
        )
        announcement.extraction_data = apply_manual_corrections(
            announcement.extraction_data,
            await _manual_corrections_for(announcement.id, db),
        )
        announcement.extraction_version = "v2"
        fields = (announcement.extraction_data or {}).get("fields") or {}
        announcement.project_code = fields.get("project_code") or announcement.project_code
        announcement.announcement_type = fields.get("announcement_type")
        announcement.content_hash = _content_hash(announcement)
    else:
        metadata = dict(announcement.source_metadata or {})
        metadata["last_recrawl"] = {
            "status": detail.detail_status,
            "at": datetime.now(TZ).isoformat(),
            "message": (detail.source_metadata or {}).get("message"),
            "failure_reason": (detail.source_metadata or {}).get("failure_reason"),
            "acquisition_mode": acquisition_mode,
        }
        announcement.source_metadata = metadata
        if not previous_full:
            announcement.detail_status = detail.detail_status
            announcement.detail_url = detail.detail_url or detail.source_url
            announcement.content_format = detail.content_format
            announcement.extraction_version = "needs_recrawl"
    await _analysis_for(announcement, db)
    await db.commit()
    await db.refresh(announcement)
    message = (
        "已重新采集、抽取并分析公告详情"
        if detail.detail_status == "full"
        else "本次详情未通过验证；已保留原有已验证正文"
        if previous_full
        else "本次未获得已验证详情，已保存真实状态"
    )
    return {
        "ok": detail.detail_status == "full",
        "message": message,
        "recrawl_status": detail.detail_status,
        "detail_status": detail.detail_status,
        "extraction_version": announcement.extraction_version,
        "acquisition_mode": acquisition_mode,
        "verification_attempted": verification_attempted,
        "failure_reason": (detail.source_metadata or {}).get("failure_reason"),
        "announcement": await _expanded_detail(announcement, db),
    }


@router.post("/{announcement_id}/analyze")
async def analyze_announcement(
    announcement_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    announcement = await db.get(TenderAnnouncement, announcement_id)
    if not announcement:
        raise HTTPException(status_code=404, detail="公告不存在")
    analysis = await _analysis_for(announcement, db)
    await db.commit()
    return {"ok": True, "analysis": analysis, "message": "已按当前企业画像重新分析"}


@router.patch("/{announcement_id}/fields")
async def correct_announcement_fields(
    announcement_id: str,
    body: FieldCorrectionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    announcement = await db.get(TenderAnnouncement, announcement_id)
    if not announcement:
        raise HTTPException(status_code=404, detail="公告不存在")
    invalid = sorted(set(body.fields) - _CORRECTABLE_FIELDS)
    if invalid:
        raise HTTPException(status_code=422, detail=f"不允许校正的字段: {', '.join(invalid)}")
    if not body.fields:
        raise HTTPException(status_code=422, detail="至少提交一个校正字段")

    extraction = dict(announcement.extraction_data or {})
    fields = dict(extraction.get("fields") or {})
    records = dict(extraction.get("field_records") or {})
    evidence = dict(extraction.get("evidence") or {})
    now = datetime.now(TZ)
    for field_name, corrected_value in body.fields.items():
        previous = fields.get(field_name)
        fields[field_name] = corrected_value
        record = {
            "evidence_id": f"M-{field_name}-{now.strftime('%Y%m%d%H%M%S')}",
            "value": corrected_value,
            "source_label": "人工校正",
            "page": None,
            "quote": None,
            "method": "manual_correction",
            "status": "corrected",
            "reason": body.reason,
            "corrected_at": now.isoformat(),
        }
        records[field_name] = record
        evidence[field_name] = record
        db.add(
            AnnouncementFieldCorrection(
                announcement_id=announcement.id,
                field_name=field_name,
                previous_value=previous,
                corrected_value=corrected_value,
                reason=body.reason,
                corrected_at=now,
            )
        )
    extraction.update(
        {
            "version": 2,
            "extraction_version": "v2",
            "fields": fields,
            "field_records": records,
            "evidence": evidence,
            "manual_correction_count": int(extraction.get("manual_correction_count") or 0)
            + len(body.fields),
        }
    )
    announcement.extraction_data = extraction
    announcement.extraction_version = "v2"
    if "project_code" in body.fields:
        announcement.project_code = str(body.fields["project_code"] or "") or None
    if "announcement_type" in body.fields:
        announcement.announcement_type = str(body.fields["announcement_type"] or "") or None
    await _analysis_for(announcement, db)
    await db.commit()
    await db.refresh(announcement)
    return {
        "ok": True,
        "message": "人工校正已保存并写入审计记录",
        "announcement": await _expanded_detail(announcement, db),
    }
