"""多源采集编排：检索 → 详情 → 清洗 → 过滤 → 跨源去重 → 增量 → 入库."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.session import LoginRequiredError
from app.cleaners.filters import FilterContext, filter_detail, filter_list_item, simple_summary
from app.core.config import get_settings
from app.deduplication.engine import CandidateRecord, deduplicate_candidates, is_duplicate
from app.deduplication.incremental import (
    IncrementalPlan,
    mark_delivered,
    plan_incremental_delivery,
)
from app.deduplication.normalize import normalize_bid_code
from app.models.announcement import TenderAnnouncement
from app.models.company import CompanyProfile
from app.models.company import AnnouncementFieldCorrection
from app.models.execution import TaskExecution
from app.models.task import SearchTask
from app.reports.analysis import build_execution_analysis
from app.reports.fields import (
    apply_manual_corrections,
    build_extraction_data,
    build_extraction_data_with_ai,
    source_display_name,
)
from app.reports.word_report import ReportContext, SourceRunStat, generate_report_file
from app.sources.base import (
    DetailResult,
    ListItem,
    SearchQuery,
    TenderSourceAdapter,
)
from app.sources.registry import build_sources

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class CrawlStats:
    sources_requested: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)
    source_stats: list[SourceRunStat] = field(default_factory=list)
    raw_result_count: int = 0
    list_filtered_out: int = 0
    detail_cap_skipped: int = 0
    detail_failed: int = 0
    detail_status_failed_count: int = 0
    detail_success_count: int = 0
    detail_metadata_only_count: int = 0
    detail_human_verification_count: int = 0
    detail_not_attempted_count: int = 0
    cached_full_reused_count: int = 0
    detail_filtered_out: int = 0
    candidates_count: int = 0
    filtered_out_count: int = 0  # list + detail 过滤合计（兼容）
    duplicate_count: int = 0
    cross_source_merge_count: int = 0
    primary_count: int = 0
    db_merge_count: int = 0
    saved_count: int = 0
    incremental_count: int = 0
    update_count: int = 0
    skipped_already_delivered: int = 0
    announcement_ids: list[str] = field(default_factory=list)
    output_items: list[dict] = field(default_factory=list)
    dedupe_reasons: list[str] = field(default_factory=list)
    report_path: str | None = None
    report_scope: str = "incremental"
    report_mode: str = "incremental"
    deduplicate: bool = True
    truncated: bool = False
    analysis_data: dict = field(default_factory=dict)
    failure_breakdown: dict[str, int] = field(default_factory=dict)
    failure_breakdown_by_source: dict[str, dict[str, int]] = field(default_factory=dict)
    source_detail_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    effective_concurrency: dict[str, object] = field(default_factory=dict)


@dataclass
class _SourceDiscovery:
    source: TenderSourceAdapter
    source_stat: SourceRunStat
    items: list[ListItem] = field(default_factory=list)
    status: str = "success"
    error: str | None = None
    list_filtered_out: int = 0
    detail_cap_skipped: int = 0
    truncated: bool = False


@dataclass
class _ProcessedDetail:
    source: TenderSourceAdapter
    item: ListItem
    detail: DetailResult
    candidate: CandidateRecord | None
    detail_meta: dict
    accepted: bool = True
    acquisition_exception: bool = False
    extraction_failed: bool = False
    detail_duration_ms: int = 0
    extraction_duration_ms: int = 0


class _CebpubRunController:
    """单次执行内的 CEBPUB 并发、降速和断路状态。"""

    def __init__(self, *, concurrency: int, block_threshold: int) -> None:
        self.configured_concurrency = max(1, concurrency)
        self.block_threshold = max(1, block_threshold)
        self._condition = asyncio.Condition()
        self._active = 0
        self._block_streak = 0
        self._cooldown_until = 0.0
        self.degraded = False
        self.circuit_open = False

    @property
    def effective_limit(self) -> int:
        return 1 if self.degraded else self.configured_concurrency

    async def acquire(self) -> bool:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self.circuit_open or self._active < self.effective_limit
            )
            if self.circuit_open:
                return False
            self._active += 1
            cooldown = max(0.0, self._cooldown_until - time.monotonic())
        if cooldown:
            await asyncio.sleep(cooldown)
        return True

    async def release(self) -> None:
        async with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    async def observe(self, detail: DetailResult) -> None:
        metadata = detail.source_metadata or {}
        reason = str(metadata.get("failure_reason") or "")
        blocked = bool(metadata.get("site_blocked")) or reason in {
            "verification_required",
            "verification_timeout",
            "site_rate_limited",
        }
        async with self._condition:
            if blocked:
                self._block_streak += 1
                self.degraded = True
                self._cooldown_until = max(self._cooldown_until, time.monotonic() + 3)
                if self._block_streak >= self.block_threshold:
                    self.circuit_open = True
            else:
                self._block_streak = 0
            self._condition.notify_all()


def _metadata_detail(
    item: ListItem,
    *,
    status: str,
    attempt_state: str,
    reason: str,
    message: str,
    failure_stage: str,
) -> DetailResult:
    content = "\n".join(
        value for value in (item.title, item.snippet or "") if value
    )
    return DetailResult(
        title=item.title,
        source_url=item.source_url,
        publish_time=item.publish_time,
        region=item.region,
        raw_content=content,
        clean_content=content,
        attachment_links=[],
        detail_fetched=False,
        detail_status=status,
        detail_url=item.source_url,
        source_metadata={
            "detail_status": status,
            "detail_attempt_state": attempt_state,
            "failure_reason": reason,
            "failure_stage": failure_stage,
            "message": message,
        },
        raw=dict(item.raw or {}),
    )


def _failure_bucket(detail: DetailResult, *, extraction_failed: bool = False) -> str | None:
    if extraction_failed:
        return "extraction_failure"
    metadata = detail.source_metadata or {}
    attempt_state = metadata.get("detail_attempt_state")
    reason = str(metadata.get("failure_reason") or "")
    if attempt_state == "not_attempted":
        return "not_attempted"
    if attempt_state == "blocked" or reason in {
        "site_rate_limited",
        "verification_required",
        "verification_timeout",
        "site_block_circuit_open",
    }:
        return "site_blocked"
    if reason in {
        "browser_closed",
        "managed_browser_unavailable",
        "collector_error",
        "collector_exception",
    }:
        return "browser_failure"
    if reason == "html_parse_failure":
        return "html_parse_failure"
    if reason == "html_content_empty":
        return "html_content_empty"
    if reason == "http_detail_fetch_failure":
        return "http_detail_failure"
    if reason in {
        "content_unavailable",
        "incomplete_pdf_pages",
        "pdf_not_ready",
        "pdf_not_loaded",
        "pdf_document_unavailable",
        "pdf_bytes_timeout",
        "pdf_too_large",
        "pdf_page_limit",
        "pdf_parse_failure",
        "ocr_failure",
        "collector_timeout",
    }:
        return "pdf_incomplete"
    if reason == "outer_detail_unavailable":
        return "outer_detail_unavailable"
    if reason == "official_content_unavailable":
        return "official_content_unavailable"
    if reason in {"identity_mismatch", "pdf_title_mismatch"}:
        return "identity_conflict"
    if detail.detail_status != "full":
        return "metadata_only_other"
    return None


def _content_hash(title: str, content: str, url: str) -> str:
    raw = f"{title}|{content[:2000]}|{url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_dedupe_key(source_name: str, item_id: str | None, url: str, title: str) -> str:
    if item_id:
        return f"{source_name}:{item_id}"
    return f"{source_name}:{hashlib.md5((url or title).encode()).hexdigest()}"


def _snapshot_record_id(record: CandidateRecord, index: int) -> str:
    """Return an opaque report-row identifier without pretending it is a DB ID."""
    identity = "|".join(
        [
            record.source_name or "",
            record.source_item_id or "",
            record.source_url or "",
            record.content_hash or "",
            str(index),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"snapshot:{index + 1}:{digest}"


def _snapshot_duplicate_hints(records: list[CandidateRecord]) -> list[dict[str, object]]:
    """Describe possible same-batch duplicates without removing any record.

    ``deduplicate_candidates`` mutates primary records while merging.  The
    snapshot report needs the pre-merge source records, so it uses the same
    duplicate predicate only to disclose possible duplicates to the reader.
    """
    reasons: list[list[str]] = [[] for _ in records]
    for right_index, right in enumerate(records):
        for left_index in range(right_index):
            is_same, reason = is_duplicate(records[left_index], right)
            if not is_same:
                continue
            if reason:
                reasons[left_index].append(reason)
                reasons[right_index].append(reason)

    hints: list[dict[str, object]] = []
    for item_reasons in reasons:
        unique_reasons = list(dict.fromkeys(item_reasons))
        hints.append(
            {
                "dedupe_status": "保留（同批疑似重复）"
                if unique_reasons
                else "保留（未发现同批重复）",
                "dedupe_hint": "；".join(unique_reasons),
                "dedupe_reasons": unique_reasons,
            }
        )
    return hints


def _build_snapshot_output_items(
    records: list[CandidateRecord], detail_meta: dict[str, dict],
) -> list[dict]:
    """Build a report-only, non-deduplicated view of this crawl batch.

    This intentionally runs *before* the database and cross-source merge
    steps.  Each accepted source record remains a separate report item, while
    the durable announcement store and DeliveryHistory continue to use their
    existing deduplicated primary records.
    """
    hints = _snapshot_duplicate_hints(records)
    items: list[dict] = []
    for index, (record, hint) in enumerate(zip(records, hints, strict=True)):
        meta = detail_meta.get(record.source_url) or {}
        row_id = _snapshot_record_id(record, index)
        items.append(
            {
                "announcement_id": row_id,
                "report_item_id": row_id,
                "snapshot_record": True,
                "source_item_id": record.source_item_id,
                "title": record.title,
                "source_name": record.source_name,
                "source_url": record.source_url,
                "related_urls": [record.source_url] if record.source_url else [],
                "related_sources": [],
                "dedupe_reasons": hint["dedupe_reasons"],
                "dedupe_status": hint["dedupe_status"],
                "dedupe_hint": hint["dedupe_hint"],
                "is_new": False,
                "is_update": False,
                "change_label": "本轮原始记录（未去重）",
                "content_hash": record.content_hash,
                "summary": record.summary,
                "clean_content": (record.clean_content or "")[:8000],
                "publish_time": record.publish_time.isoformat() if record.publish_time else None,
                "region": record.region,
                "project_code": record.project_code,
                "announcement_type": record.announcement_type,
                "detail_status": record.detail_status or meta.get("detail_status", "unknown"),
                "source_metadata": record.source_metadata
                or meta.get("source_metadata")
                or {},
                "extraction_data": record.extraction_data or {},
                "attachment_links": record.attachment_links or [],
                "data_mode": record.data_mode,
                "requires_login": record.requires_login,
                "detail_fetched": meta.get(
                    "detail_fetched", record.detail_status == "full"
                ),
                "attachment_extract_failed": meta.get(
                    "attachment_extract_failed", False
                ),
                "detail_url": meta.get("detail_url") or record.source_url,
                "content_format": meta.get("content_format"),
            }
        )
    return items


async def _discover_source(
    source: TenderSourceAdapter,
    *,
    query: SearchQuery,
    ctx: FilterContext,
    detail_cap: int,
    report_mode: str,
    semaphore: asyncio.Semaphore,
) -> _SourceDiscovery:
    source_stat = SourceRunStat(
        source_name=source.source_name,
        display_name=getattr(source, "display_name", None)
        or source_display_name(source.source_name),
    )
    try:
        async with semaphore:
            if source.requires_login:
                health = await source.health_check()
                if not health.ok or health.login_ok is False:
                    message = health.message or "登录态不可用，已跳过（公开源继续）"
                    source_stat.status = "skipped"
                    source_stat.message = message
                    return _SourceDiscovery(
                        source=source,
                        source_stat=source_stat,
                        status="skipped",
                        error=message,
                    )
            list_items = await source.search(query)
        kept: list[ListItem] = []
        filtered_out = 0
        for item in list_items:
            result = filter_list_item(item, ctx)
            if result.accepted:
                kept.append(item)
            else:
                filtered_out += 1
        source_stat.raw_count = len(list_items)
        source_stat.list_kept = len(kept)
        source_stat.status = "success"
        source_stat.message = "检索完成"
        return _SourceDiscovery(
            source=source,
            source_stat=source_stat,
            items=kept[:detail_cap],
            list_filtered_out=filtered_out,
            detail_cap_skipped=max(0, len(kept) - detail_cap),
            truncated=report_mode == "full_snapshot" and len(kept) >= 500,
        )
    except LoginRequiredError as exc:
        message = str(exc)
        source_stat.status = "skipped"
        source_stat.message = message
        return _SourceDiscovery(
            source=source,
            source_stat=source_stat,
            status="skipped",
            error=message,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        logger.exception("source %s failed", source.source_name)
        source_stat.status = "failed"
        source_stat.message = message
        return _SourceDiscovery(
            source=source,
            source_stat=source_stat,
            status="failed",
            error=message,
        )


async def _process_detail_work(
    source: TenderSourceAdapter,
    item: ListItem,
    *,
    ctx: FilterContext,
    keywords: list[str],
    source_semaphore: asyncio.Semaphore,
    llm_semaphore: asyncio.Semaphore,
    cebpub_controller: _CebpubRunController,
) -> _ProcessedDetail:
    detail_started = time.monotonic()
    acquisition_exception = False
    attachment_extract_failed = False
    detail: DetailResult | None = None

    async def fetch() -> DetailResult:
        nonlocal attachment_extract_failed
        detail_value = await source.fetch_detail(item, interactive=False)
        try:
            attachments = await source.extract_attachments(detail_value)
            detail_value.attachment_links = attachments or []
        except Exception as attachment_error:  # noqa: BLE001
            logger.warning(
                "%s attach extract fail %s: %s",
                source.source_name,
                item.source_url,
                attachment_error,
            )
            detail_value.attachment_links = list(detail_value.attachment_links or [])
            attachment_extract_failed = True
        return detail_value

    if source.source_name == "cebpub":
        allowed = await cebpub_controller.acquire()
        if not allowed:
            detail = _metadata_detail(
                item,
                status="metadata_only",
                attempt_state="not_attempted",
                reason="site_block_circuit_open",
                message="本轮官方站点已连续阻断，剩余公告未继续请求",
                failure_stage="navigation",
            )
        else:
            try:
                detail = await fetch()
            except Exception as exc:  # noqa: BLE001
                acquisition_exception = True
                logger.warning("%s detail fail %s: %s", source.source_name, item.source_url, exc)
                detail = _metadata_detail(
                    item,
                    status="failed",
                    attempt_state="attempted",
                    reason="collector_exception",
                    message=f"详情采集异常：{type(exc).__name__}",
                    failure_stage="detail_acquisition",
                )
            finally:
                try:
                    if detail is not None:
                        await cebpub_controller.observe(detail)
                        if cebpub_controller.degraded:
                            try:
                                from app.browser.managed_public import (
                                    get_managed_public_browser,
                                )

                                get_managed_public_browser().set_adaptive_mode(True)
                            except Exception:  # noqa: BLE001
                                pass
                finally:
                    await cebpub_controller.release()
    else:
        try:
            async with source_semaphore:
                detail = await fetch()
        except Exception as exc:  # noqa: BLE001
            acquisition_exception = True
            logger.warning("%s detail fail %s: %s", source.source_name, item.source_url, exc)
            reason = str(getattr(exc, "failure_reason", "collector_exception"))
            stage = str(getattr(exc, "failure_stage", "detail_acquisition"))
            detail = _metadata_detail(
                item,
                status="failed",
                attempt_state="attempted",
                reason=reason,
                message=f"详情采集异常：{type(exc).__name__}",
                failure_stage=stage,
            )

    detail_duration_ms = int((time.monotonic() - detail_started) * 1000)
    assert detail is not None
    metadata = dict(detail.source_metadata or {})
    metadata.setdefault("detail_attempt_state", "attempted")
    metadata.setdefault("duration_ms", detail_duration_ms)
    detail.source_metadata = metadata
    detail_filter = filter_detail(detail, ctx)
    detail_meta = {
        "detail_fetched": detail.detail_fetched,
        "detail_status": detail.detail_status,
        "source_metadata": detail.source_metadata,
        "attachment_extract_failed": attachment_extract_failed,
        "requires_login": source.requires_login,
        "detail_url": detail.detail_url or detail.source_url,
        "content_format": detail.content_format,
    }
    if not detail_filter.accepted:
        return _ProcessedDetail(
            source=source,
            item=item,
            detail=detail,
            candidate=None,
            detail_meta=detail_meta,
            accepted=False,
            acquisition_exception=acquisition_exception,
            detail_duration_ms=detail_duration_ms,
        )

    dedupe_key = _source_dedupe_key(
        source.source_name, item.source_item_id, detail.source_url, detail.title
    )
    content_hash = _content_hash(detail.title, detail.clean_content, detail.source_url)
    summary = simple_summary(detail.title, detail.clean_content)
    project_code = normalize_bid_code(f"{detail.title} {detail.clean_content or ''}")
    extraction_started = time.monotonic()
    extraction_failed = False
    try:
        async with llm_semaphore:
            extraction_data = await build_extraction_data_with_ai(
                title=detail.title,
                clean_content=detail.clean_content,
                summary=summary,
                region=detail.region or item.region,
                project_code=project_code,
                publish_time=detail.publish_time,
                detail_status=detail.detail_status,
                source_metadata=detail.source_metadata,
            )
        detail.source_metadata["extraction_status"] = "completed"
    except Exception as exc:  # noqa: BLE001
        extraction_failed = True
        logger.warning(
            "%s extraction failed %s: %s",
            source.source_name,
            item.source_url,
            type(exc).__name__,
        )
        extraction_data = build_extraction_data(
            title=detail.title,
            clean_content=detail.clean_content,
            summary=summary,
            region=detail.region or item.region,
            project_code=project_code,
            publish_time=detail.publish_time,
            detail_status=detail.detail_status,
            source_metadata=detail.source_metadata,
        )
        detail.source_metadata["extraction_status"] = "rule_fallback_after_error"
        detail.source_metadata["extraction_failure"] = type(exc).__name__
    extraction_duration_ms = int((time.monotonic() - extraction_started) * 1000)
    detail.source_metadata["extraction_duration_ms"] = extraction_duration_ms
    detail_meta["source_metadata"] = detail.source_metadata
    announcement_type = (extraction_data.get("fields") or {}).get("announcement_type")
    candidate = CandidateRecord(
        title=detail.title[:512],
        source_name=source.source_name,
        source_url=detail.source_url,
        source_item_id=item.source_item_id,
        requires_login=source.requires_login,
        data_mode="live",
        publish_time=detail.publish_time,
        region=detail.region or item.region,
        province=None,
        keywords=keywords,
        summary=summary,
        clean_content=detail.clean_content,
        raw_content=(detail.raw_content or "")[:500000],
        detail_status=detail.detail_status,
        source_metadata=detail.source_metadata,
        extraction_data=extraction_data,
        attachment_links=list(detail.attachment_links or []),
        content_hash=content_hash,
        deduplication_key=dedupe_key,
        project_code=project_code,
        announcement_type=announcement_type,
    )
    return _ProcessedDetail(
        source=source,
        item=item,
        detail=detail,
        candidate=candidate,
        detail_meta=detail_meta,
        acquisition_exception=acquisition_exception,
        extraction_failed=extraction_failed,
        detail_duration_ms=detail_duration_ms,
        extraction_duration_ms=extraction_duration_ms,
    )


async def execute_search_task(
    db: AsyncSession,
    task: SearchTask,
    *,
    max_details_per_source: int | None = None,
    trigger_type: str = "manual",
    report_mode: str | None = None,
    report_scope: str | None = None,
) -> tuple[TaskExecution, CrawlStats]:
    settings = get_settings()
    execution_wall_started = time.monotonic()
    if report_mode is None:
        report_mode = "full_snapshot" if report_scope == "snapshot" else "incremental"
    report_mode = (
        report_mode if report_mode in {"incremental", "full_snapshot"} else "incremental"
    )
    report_scope = "snapshot" if report_mode == "full_snapshot" else "incremental"
    detail_cap = max_details_per_source
    if detail_cap is None:
        detail_cap = 500 if report_mode == "full_snapshot" else 8
    detail_cap = min(max(int(detail_cap), 1), 500)
    stats = CrawlStats(
        report_scope=report_scope,
        report_mode=report_mode,
        deduplicate=report_mode != "full_snapshot",
    )
    now = datetime.now(TZ)

    execution = TaskExecution(
        task_id=task.id,
        started_at=now,
        status="running",
        trigger_type=trigger_type,
        report_scope=report_scope,
        report_mode=report_mode,
        deduplicate=report_mode != "full_snapshot",
        truncated=False,
        sources_requested=[],
        sources_succeeded=[],
        raw_result_count=0,
        filtered_result_count=0,
        duplicate_count=0,
        incremental_count=0,
    )
    db.add(execution)
    await db.flush()

    task.status = "running"
    await db.commit()
    await db.refresh(execution)

    keywords = list(task.keywords or [])
    regions = list(task.regions or [])
    start: date | None = task.start_date
    end: date | None = task.end_date
    ctx = FilterContext(keywords=keywords, regions=regions, start_date=start, end_date=end)
    query = SearchQuery(
        keywords=keywords,
        regions=regions,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
    )

    sources = build_sources(only_enabled=True)
    stats.sources_requested = [s.source_name for s in sources]
    execution.sources_requested = stats.sources_requested

    errors: list[str] = []
    candidates: list[CandidateRecord] = []
    # 详情失败等元数据，供报告附件状态
    detail_meta: dict[str, dict] = {}  # key: source_url -> flags
    discovery_started = time.monotonic()
    discovery_semaphore = asyncio.Semaphore(settings.crawl_max_concurrency)
    discoveries = await asyncio.gather(
        *(
            _discover_source(
                source,
                query=query,
                ctx=ctx,
                detail_cap=detail_cap,
                report_mode=report_mode,
                semaphore=discovery_semaphore,
            )
            for source in sources
        )
    )
    stats.stage_durations_ms["discovery_wall"] = int(
        (time.monotonic() - discovery_started) * 1000
    )

    work: list[tuple[TenderSourceAdapter, ListItem]] = []
    for discovery in discoveries:
        stats.source_stats.append(discovery.source_stat)
        stats.raw_result_count += discovery.source_stat.raw_count
        stats.list_filtered_out += discovery.list_filtered_out
        stats.filtered_out_count += discovery.list_filtered_out
        stats.detail_cap_skipped += discovery.detail_cap_skipped
        stats.truncated = stats.truncated or discovery.truncated
        if discovery.status == "success":
            stats.sources_succeeded.append(discovery.source.source_name)
            work.extend((discovery.source, item) for item in discovery.items)
        else:
            message = discovery.error or "数据源未完成"
            stats.sources_failed[discovery.source.source_name] = message
            if discovery.status == "failed":
                errors.append(f"{discovery.source.source_name}: {message}")

    public_detail_semaphore = asyncio.Semaphore(settings.crawl_max_concurrency)
    login_detail_semaphore = asyncio.Semaphore(1)
    llm_semaphore = asyncio.Semaphore(settings.llm_extraction_concurrency)
    cebpub_controller = _CebpubRunController(
        concurrency=settings.cebpub_browser_concurrency,
        block_threshold=settings.cebpub_site_block_threshold,
    )
    try:
        from app.browser.managed_public import get_managed_public_browser

        managed_browser = get_managed_public_browser()
        managed_browser.set_adaptive_mode(False)
    except Exception:  # noqa: BLE001
        managed_browser = None

    pipeline_started = time.monotonic()
    processed_details: list[_ProcessedDetail] = []
    try:
        processed_details = await asyncio.gather(
            *(
                _process_detail_work(
                    source,
                    item,
                    ctx=ctx,
                    keywords=keywords,
                    source_semaphore=(
                        login_detail_semaphore
                        if source.requires_login
                        else public_detail_semaphore
                    ),
                    llm_semaphore=llm_semaphore,
                    cebpub_controller=cebpub_controller,
                )
                for source, item in work
            )
        )
    finally:
        if managed_browser is not None:
            managed_browser.set_adaptive_mode(False)
    stats.stage_durations_ms["detail_pipeline_wall"] = int(
        (time.monotonic() - pipeline_started) * 1000
    )
    stats.stage_durations_ms["detail_work_total"] = sum(
        result.detail_duration_ms for result in processed_details
    )
    stats.stage_durations_ms["extraction_work_total"] = sum(
        result.extraction_duration_ms for result in processed_details
    )
    source_stats_by_name = {row.source_name: row for row in stats.source_stats}
    for result in processed_details:
        detail = result.detail
        source_name = result.source.source_name
        source_stat = source_stats_by_name[source_name]
        detail_meta[detail.source_url] = result.detail_meta
        attempt_state = (detail.source_metadata or {}).get("detail_attempt_state")
        if attempt_state == "not_attempted":
            stats.detail_not_attempted_count += 1
            source_stat.detail_metadata_only += 1
            detail_outcome = "not_attempted"
        elif result.acquisition_exception:
            stats.detail_failed += 1
            source_stat.detail_metadata_only += 1
            detail_outcome = "failed"
        elif detail.detail_fetched and detail.detail_status == "full":
            stats.detail_success_count += 1
            source_stat.detail_success += 1
            detail_outcome = "full"
        elif detail.detail_status == "needs_human_verification":
            stats.detail_human_verification_count += 1
            source_stat.detail_metadata_only += 1
            detail_outcome = "needs_human_verification"
        elif detail.detail_status == "failed":
            stats.detail_status_failed_count += 1
            source_stat.detail_metadata_only += 1
            detail_outcome = "failed"
        else:
            stats.detail_metadata_only_count += 1
            source_stat.detail_metadata_only += 1
            detail_outcome = "metadata_only"
        source_outcomes = stats.source_detail_breakdown.setdefault(source_name, {})
        source_outcomes[detail_outcome] = source_outcomes.get(detail_outcome, 0) + 1
        bucket = _failure_bucket(detail, extraction_failed=result.extraction_failed)
        if bucket:
            stats.failure_breakdown[bucket] = stats.failure_breakdown.get(bucket, 0) + 1
            source_failures = stats.failure_breakdown_by_source.setdefault(
                source_name, {}
            )
            source_failures[bucket] = source_failures.get(bucket, 0) + 1
        if not result.accepted:
            stats.detail_filtered_out += 1
            stats.filtered_out_count += 1
            continue
        if result.candidate is not None:
            candidates.append(result.candidate)

    stats.effective_concurrency = {
        "source_discovery": settings.crawl_max_concurrency,
        "http_detail": settings.crawl_max_concurrency,
        "cebpub_browser": settings.cebpub_browser_concurrency,
        "llm_extraction": settings.llm_extraction_concurrency,
        "cebpub_adaptive": cebpub_controller.degraded,
        "cebpub_circuit_open": cebpub_controller.circuit_open,
    }

    # ---- 跨源去重（本批次）----
    postprocess_started = time.monotonic()
    stats.candidates_count = len(candidates)
    # ``snapshot`` is a report-only, source-record view.  Build it before the
    # dedupe engine mutates and merges candidate records.
    snapshot_output_items = (
        _build_snapshot_output_items(candidates, detail_meta)
        if report_mode == "full_snapshot"
        else []
    )
    deduped = deduplicate_candidates(candidates)
    stats.cross_source_merge_count = deduped.merged_count
    stats.duplicate_count = deduped.merged_count
    stats.primary_count = len(deduped.primaries)
    stats.dedupe_reasons = deduped.reasons
    # 闭合：候选 = 合并减少 + 主记录（若引擎定义不同则以实际为准）
    if stats.candidates_count and stats.cross_source_merge_count + stats.primary_count != stats.candidates_count:
        # 引擎 merged_count 语义若非「减少条数」，校正为差值
        stats.cross_source_merge_count = max(0, stats.candidates_count - stats.primary_count)
        stats.duplicate_count = stats.cross_source_merge_count

    crawl_time = datetime.now(TZ)
    saved_ids: list[str] = []
    id_hash_pairs: list[tuple[str, str]] = []

    for rec in deduped.primaries:
        # 库内：按全局内容/项目编号/标准化去重键查找已有主记录
        existing = None
        lifecycle_siblings: list[TenderAnnouncement] = []
        if rec.project_code:
            sibling_stmt = select(TenderAnnouncement).where(
                TenderAnnouncement.project_code == rec.project_code,
                TenderAnnouncement.is_primary.is_(True),
            )
            if rec.announcement_type:
                sibling_stmt = sibling_stmt.where(
                    TenderAnnouncement.announcement_type != rec.announcement_type
                )
            lifecycle_siblings = list((await db.execute(sibling_stmt)).scalars().all())
            existing = await db.scalar(
                select(TenderAnnouncement).where(
                    TenderAnnouncement.project_code == rec.project_code,
                    TenderAnnouncement.announcement_type == rec.announcement_type,
                    TenderAnnouncement.is_primary.is_(True),
                )
            )
        if existing is None and rec.deduplication_key:
            existing = await db.scalar(
                select(TenderAnnouncement).where(
                    TenderAnnouncement.deduplication_key == rec.deduplication_key
                )
            )
        if existing is None and rec.content_hash:
            existing = await db.scalar(
                select(TenderAnnouncement).where(
                    TenderAnnouncement.content_hash == rec.content_hash,
                    TenderAnnouncement.is_primary.is_(True),
                )
            )

        if existing:
            # 合并来源 URL，不删除记录
            stats.db_merge_count += 1
            stats.duplicate_count += 1
            existing.last_seen_at = crawl_time
            urls = list(existing.related_urls or [])
            for u in rec.related_urls or [rec.source_url]:
                if u and u not in urls:
                    urls.append(u)
            existing.related_urls = urls
            rel = list(existing.related_sources or [])
            for s in rec.related_sources or []:
                rel.append(s)
            if rec.source_name != existing.source_name:
                rel.append(
                    {
                        "source_name": rec.source_name,
                        "source_url": rec.source_url,
                        "reason": "库内合并",
                    }
                )
            existing.related_sources = rel
            existing.dedupe_reasons = list(
                set((existing.dedupe_reasons or []) + (rec.dedupe_reasons or ["库内重复"]))
            )
            # 已核验的详情优先于列表元数据；同等详情质量时才按内容长度更新。
            rec_is_full = rec.detail_status == "full"
            existing_is_full = existing.detail_status == "full"
            reused_cached_full = existing_is_full and not rec_is_full
            if reused_cached_full:
                current_metadata = dict(rec.source_metadata or {})
                preserved_metadata = dict(existing.source_metadata or {})
                preserved_metadata["last_attempt"] = {
                    "attempted_at": crawl_time.isoformat(),
                    "status": rec.detail_status or "metadata_only",
                    "detail_fetched": False,
                    "detail_attempt_state": current_metadata.get(
                        "detail_attempt_state", "attempted"
                    ),
                    "failure_reason": current_metadata.get("failure_reason"),
                    "failure_stage": current_metadata.get("failure_stage"),
                    "acquisition_path": current_metadata.get("acquisition_path"),
                    "message": str(current_metadata.get("message") or "")[:500],
                    "attempt_count": int(current_metadata.get("attempt_count") or 0),
                    "duration_ms": int(current_metadata.get("duration_ms") or 0),
                }
                preserved_metadata["using_cached_full"] = True
                preserved_metadata["cached_full_captured_at"] = (
                    existing.crawl_time.isoformat() if existing.crawl_time else None
                )
                existing.source_metadata = preserved_metadata
                stats.cached_full_reused_count += 1
            if rec.clean_content and (
                (rec_is_full and not existing_is_full)
                or (
                    rec.detail_status == existing.detail_status
                    and len(rec.clean_content) > len(existing.clean_content or "")
                )
            ):
                existing.clean_content = rec.clean_content
                existing.summary = rec.summary
                existing.raw_content = rec.raw_content
                existing.detail_status = rec.detail_status
                existing.source_metadata = rec.source_metadata
                existing.extraction_data = rec.extraction_data
                existing.extraction_version = "v2"
                existing.announcement_type = rec.announcement_type
                existing.detail_url = rec.source_metadata.get("detail_url") if rec.source_metadata else rec.source_url
                existing.content_format = rec.source_metadata.get("content_format") if rec.source_metadata else None
            elif existing.detail_status in {"unknown", "failed"} and rec.detail_status:
                existing.detail_status = rec.detail_status
                existing.source_metadata = rec.source_metadata
                existing.extraction_data = rec.extraction_data
                existing.extraction_version = "v2"
                existing.announcement_type = rec.announcement_type
            elif rec_is_full and existing_is_full:
                # 即使当前正文较短，本轮已验证成功也必须清除上次“历史正文复用”标记。
                existing.source_metadata = rec.source_metadata
                existing.extraction_data = rec.extraction_data
                existing.extraction_version = "v2"
                existing.announcement_type = rec.announcement_type
                existing.detail_url = (
                    rec.source_metadata.get("detail_url")
                    if rec.source_metadata
                    else rec.source_url
                )
                existing.content_format = (
                    rec.source_metadata.get("content_format")
                    if rec.source_metadata
                    else existing.content_format
                )
            elif not rec_is_full and not existing_is_full:
                # 非完整详情也要保存本轮真实状态，不能让旧的“待验证/超时”
                # 永久覆盖后来得到的更准确失败码。
                existing.detail_status = rec.detail_status or "metadata_only"
                existing.source_metadata = rec.source_metadata
                existing.detail_url = (
                    rec.source_metadata.get("detail_url")
                    if rec.source_metadata
                    else rec.source_url
                )
                existing.content_format = (
                    rec.source_metadata.get("content_format")
                    if rec.source_metadata
                    else None
                )
            corrections = (
                await db.execute(
                    select(AnnouncementFieldCorrection)
                    .where(AnnouncementFieldCorrection.announcement_id == existing.id)
                    .order_by(AnnouncementFieldCorrection.corrected_at.asc())
                )
            ).scalars().all()
            if corrections and existing.extraction_data:
                existing.extraction_data = apply_manual_corrections(
                    existing.extraction_data, corrections
                )
            atts = list(existing.attachment_links or [])
            for a in rec.attachment_links or []:
                if a not in atts:
                    atts.append(a)
            existing.attachment_links = atts
            if rec_is_full or not existing_is_full:
                existing.content_hash = rec.content_hash or existing.content_hash
            if rec.project_code and not existing.project_code:
                existing.project_code = rec.project_code
            ann_id = existing.id
            chash = existing.content_hash or rec.content_hash or ""
        else:
            lifecycle_relations = list(rec.related_sources or [])
            for sibling in lifecycle_siblings:
                lifecycle_relations.append(
                    {
                        "source_name": sibling.source_name,
                        "source_url": sibling.source_url,
                        "announcement_id": sibling.id,
                        "announcement_type": sibling.announcement_type,
                        "relation": "lifecycle",
                        "reason": "同项目编号不同公告类型，关联但不合并",
                    }
                )
            ann = TenderAnnouncement(
                title=rec.title[:512],
                source_name=rec.source_name,
                source_url=rec.source_url,
                source_item_id=rec.source_item_id,
                requires_login=rec.requires_login,
                data_mode=rec.data_mode,
                publish_time=rec.publish_time,
                region=rec.region,
                province=rec.province,
                city=None,
                keywords=rec.keywords,
                summary=rec.summary,
                clean_content=rec.clean_content,
                raw_content=rec.raw_content,
                detail_status=rec.detail_status,
                source_metadata=rec.source_metadata,
                extraction_data=rec.extraction_data,
                extraction_version="v2",
                announcement_type=rec.announcement_type,
                detail_url=(rec.source_metadata or {}).get("detail_url") or rec.source_url,
                content_format=(rec.source_metadata or {}).get("content_format"),
                attachment_links=rec.attachment_links or [],
                crawl_time=crawl_time,
                content_hash=rec.content_hash,
                deduplication_key=rec.deduplication_key,
                is_primary=True,
                related_urls=rec.related_urls or [rec.source_url],
                related_sources=lifecycle_relations,
                dedupe_reasons=rec.dedupe_reasons or [],
                project_code=rec.project_code,
                first_seen_at=crawl_time,
                last_seen_at=crawl_time,
            )
            db.add(ann)
            await db.flush()
            for sibling in lifecycle_siblings:
                reverse = list(sibling.related_sources or [])
                if not any(
                    isinstance(row, dict) and row.get("announcement_id") == ann.id
                    for row in reverse
                ):
                    reverse.append(
                        {
                            "source_name": ann.source_name,
                            "source_url": ann.source_url,
                            "announcement_id": ann.id,
                            "announcement_type": ann.announcement_type,
                            "relation": "lifecycle",
                            "reason": "同项目编号不同公告类型，关联但不合并",
                        }
                    )
                    sibling.related_sources = reverse
            stats.saved_count += 1
            ann_id = ann.id
            chash = rec.content_hash or ""

        saved_ids.append(ann_id)
        id_hash_pairs.append((ann_id, chash))

    # ---- 增量：相对本任务 DeliveryHistory ----
    # 完整快照是独立审计输出，不读取、不写入增量交付历史。
    if report_mode == "full_snapshot":
        plan = IncrementalPlan(items=[], new_count=0, update_count=0, skipped_count=0)
    else:
        plan = await plan_incremental_delivery(
            db, task_id=task.id, announcements=id_hash_pairs
        )
    stats.incremental_count = plan.new_count
    stats.update_count = plan.update_count
    stats.skipped_already_delivered = plan.skipped_count
    stats.announcement_ids = saved_ids

    output_items: list[dict] = (
        snapshot_output_items if report_mode == "full_snapshot" else []
    )
    planned_by_id = {item.announcement_id: item for item in plan.items}
    # 增量报告只展示未交付的变更；未去重快照已保留本轮每条来源记录，
    # 不再经数据库主记录回读，避免跨源或库内合并吞掉报告条目。
    report_ids = (
        [] if report_mode == "full_snapshot" else list(planned_by_id)
    )
    for announcement_id in report_ids:
        ann = await db.get(TenderAnnouncement, announcement_id)
        if not ann:
            continue
        item = planned_by_id.get(ann.id)
        meta = detail_meta.get(ann.source_url) or {
            "detail_fetched": ann.detail_status == "full",
            "detail_status": ann.detail_status or "unknown",
            "attachment_extract_failed": False,
            "requires_login": ann.requires_login,
        }
        stored_metadata = ann.source_metadata or meta.get("source_metadata") or {}
        cached_full_reused = bool(stored_metadata.get("using_cached_full"))
        output_items.append(
            {
                "announcement_id": ann.id,
                "title": ann.title,
                "source_name": ann.source_name,
                "source_url": ann.source_url,
                "related_urls": ann.related_urls or [ann.source_url],
                "related_sources": ann.related_sources or [],
                "dedupe_reasons": ann.dedupe_reasons or [],
                "is_new": bool(item and item.is_new),
                "is_update": bool(item and item.is_update),
                "change_label": (
                    "内容发生变化"
                    if item and item.is_update
                    else ("新增" if item and item.is_new else "")
                ),
                "content_hash": item.content_hash if item else ann.content_hash,
                "summary": ann.summary,
                "clean_content": (ann.clean_content or "")[:8000],
                "publish_time": ann.publish_time.isoformat() if ann.publish_time else None,
                "region": ann.region,
                "project_code": ann.project_code,
                "announcement_type": ann.announcement_type,
                "detail_status": ann.detail_status or meta.get("detail_status", "unknown"),
                "source_metadata": stored_metadata,
                "extraction_data": ann.extraction_data,
                "attachment_links": ann.attachment_links or [],
                "data_mode": ann.data_mode,
                "requires_login": ann.requires_login,
                "detail_fetched": ann.detail_status == "full",
                "current_attempt_detail_fetched": meta.get(
                    "detail_fetched", not cached_full_reused
                ),
                "cached_full_reused": cached_full_reused,
                "attachment_extract_failed": meta.get("attachment_extract_failed", False),
                "detail_url": ann.detail_url or ann.source_url,
                "content_format": ann.content_format,
            }
        )
    stats.output_items = output_items

    # 规则分析不影响采集成功与否；可选 LLM 仅在证据校验后补充简短研判。
    try:
        profile_row = await db.scalar(
            select(CompanyProfile).order_by(CompanyProfile.updated_at.desc())
        )
        analysis = await build_execution_analysis(
            output_items,
            keywords=keywords,
            regions=regions,
            start_date=start.isoformat() if start else None,
            end_date=end.isoformat() if end else None,
            company_profile=profile_row.profile_data if profile_row else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("execution analysis unavailable: %s", exc)
        analysis = {
            "version": 1,
            "status": "rule_unavailable",
            "provider": "rules",
            "portfolio_summary": "本轮未能生成机会研判；公告原始字段与报告仍可用。",
            "projects": [],
        }
    stats.analysis_data = analysis
    execution.analysis_data = analysis
    analysis_by_id = {
        str(project.get("announcement_id")): project
        for project in analysis.get("projects", [])
        if isinstance(project, dict) and project.get("announcement_id")
    }
    for output_item in output_items:
        project = analysis_by_id.get(str(output_item.get("announcement_id")))
        if project:
            output_item["analysis_data"] = project
            announcement_id = str(output_item.get("announcement_id") or "")
            if not announcement_id.startswith("snapshot:"):
                announcement = await db.get(TenderAnnouncement, announcement_id)
                if announcement:
                    announcement.analysis_data = project

    # 各源报告贡献
    contrib_map: dict[str, int] = {}
    for it in output_items:
        sn = it.get("source_name") or ""
        contrib_map[sn] = contrib_map.get(sn, 0) + 1
    for ss in stats.source_stats:
        ss.final_contributed = contrib_map.get(ss.source_name, 0)

    # 执行结果状态
    execution.finished_at = datetime.now(TZ)
    execution.sources_succeeded = stats.sources_succeeded
    execution.raw_result_count = stats.raw_result_count
    execution.filtered_result_count = len(deduped.primaries)
    execution.duplicate_count = stats.duplicate_count
    execution.incremental_count = plan.new_count + plan.update_count
    execution.truncated = stats.truncated
    execution.detail_full_count = stats.detail_success_count
    execution.detail_metadata_count = stats.detail_metadata_only_count
    execution.detail_failed_count = stats.detail_failed + stats.detail_status_failed_count
    execution.detail_human_verification_count = stats.detail_human_verification_count

    if stats.sources_succeeded:
        has_quality_failures = bool(
            stats.sources_failed
            or stats.detail_failed
            or stats.detail_status_failed_count
            or stats.detail_metadata_only_count
            or stats.detail_human_verification_count
            or stats.detail_not_attempted_count
            or stats.failure_breakdown.get("extraction_failure", 0)
            or stats.truncated
        )
        execution.status = "partial" if has_quality_failures else "success"
    else:
        execution.status = "failed"
    if errors:
        execution.error_message = "; ".join(errors)[:4000]

    # ---- Word 报告（每次执行均生成；失败也生成说明报告）----
    settings = get_settings()
    data_modes = {it.get("data_mode") or "live" for it in output_items}
    if not output_items:
        data_mode_label = "实时数据"
    elif data_modes == {"live"}:
        data_mode_label = "实时数据"
    elif "fixture" in data_modes and "live" in data_modes:
        data_mode_label = "实时数据+演示数据"
    elif "fixture" in data_modes:
        data_mode_label = "演示数据"
    else:
        data_mode_label = "实时数据"

    schedule_desc = "无（立即执行）"
    if task.schedule_enabled:
        schedule_desc = f"{task.schedule_type or '定时'} {task.execute_time or ''}".strip()

    warnings: list[str] = []
    if execution.status == "partial":
        warnings.append(
            "存在失败或跳过的数据源，报告覆盖范围不完整，详见数据源执行情况表。"
        )
    if stats.sources_failed:
        for sn, reason in stats.sources_failed.items():
            warnings.append(f"{source_display_name(sn)}：{reason}")
    if stats.detail_human_verification_count:
        warnings.append(
            f"{stats.detail_human_verification_count} 条详情要求人工完成安全验证；"
            "系统未绕过验证，该类记录不按完整正文分析。"
        )
    if stats.detail_not_attempted_count:
        warnings.append(
            f"{stats.detail_not_attempted_count} 条详情因官方站点连续阻断而未继续请求；"
            "这些记录不计为采集失败，仍保留列表元数据。"
        )
    if stats.cached_full_reused_count:
        warnings.append(
            f"{stats.cached_full_reused_count} 条公告本轮详情采集失败，但库中已有经校验的完整正文；"
            "报告继续使用历史完整正文，并在项目详情中单独标注，不计为本轮采集成功。"
        )
    if stats.truncated:
        warnings.append(
            "本次快照已达每源 500 条安全上限，truncated=true；"
            "报告不声称为完整覆盖。"
        )

    report_ctx = ReportContext(
        original_query=task.original_query,
        generated_at=execution.finished_at or datetime.now(TZ),
        execute_type={
            "scheduled": "定时执行",
            "initial": "创建后首轮执行",
            "manual": "手工执行",
        }.get(trigger_type, "手工执行"),
        data_mode=data_mode_label,
        execution_status=execution.status or "success",
        keywords=keywords,
        regions=regions,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        schedule_desc=schedule_desc,
        sources=stats.sources_succeeded or stats.sources_requested,
        sources_requested=list(stats.sources_requested),
        sources_succeeded=list(stats.sources_succeeded),
        sources_failed=dict(stats.sources_failed),
        source_stats=list(stats.source_stats),
        raw_result_count=stats.raw_result_count,
        list_filtered_out=stats.list_filtered_out,
        detail_cap_skipped=stats.detail_cap_skipped,
        detail_failed=stats.detail_failed,
        detail_success_count=stats.detail_success_count,
        detail_metadata_only_count=stats.detail_metadata_only_count,
        detail_status_failed_count=stats.detail_status_failed_count,
        detail_human_verification_count=stats.detail_human_verification_count,
        detail_filtered_out=stats.detail_filtered_out,
        candidates_count=stats.candidates_count,
        filtered_out_count=stats.filtered_out_count,
        cross_source_merge_count=stats.cross_source_merge_count,
        primary_count=stats.primary_count or len(deduped.primaries),
        db_merge_count=stats.db_merge_count,
        duplicate_count=stats.duplicate_count,
        final_count=len(deduped.primaries),
        incremental_count=plan.new_count,
        update_count=plan.update_count,
        skipped_already_delivered=plan.skipped_count,
        report_scope=report_scope,
        truncated=stats.truncated,
        deduplicate=stats.deduplicate,
        analysis=analysis,
        items=output_items,
        crawl_time=crawl_time.isoformat(),
        warnings=warnings,
        extra_notes=(
            [f"编排错误摘要：{execution.error_message}"] if execution.error_message else []
        ),
    )
    try:
        report_path = generate_report_file(report_ctx, reports_dir=settings.reports_dir)
        execution.report_path = str(report_path)
        stats.report_path = str(report_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("report generation failed")
        # 报告失败：不标记已交付，避免未输出内容被记为已推送
        if execution.status != "failed":
            execution.status = "partial"
        msg = f"报告生成失败: {exc}"
        execution.error_message = (
            f"{execution.error_message}; {msg}" if execution.error_message else msg
        )[:4000]
        report_path = None

    # 仅成功写出报告后标记交付；失败执行或报告失败不写 DeliveryHistory
    if (
        execution.status in ("success", "partial")
        and report_mode == "incremental"
        and plan.items
        and report_path is not None
    ):
        await mark_delivered(
            db,
            task_id=task.id,
            items=plan.items,
            report_id=report_path.name,
            now=execution.finished_at,
        )

    # 首轮或手工执行不应破坏后续调度；最近一次执行状态由 TaskExecution 表达。
    if task.schedule_enabled and not task.is_paused:
        task.status = "scheduled"
    else:
        task.status = "done" if execution.status in ("success", "partial") else "failed"
    stats.stage_durations_ms["postprocess_report_wall"] = int(
        (time.monotonic() - postprocess_started) * 1000
    )
    stats.stage_durations_ms["total_wall"] = int(
        (time.monotonic() - execution_wall_started) * 1000
    )
    execution.crawl_diagnostics = {
        "detail_not_attempted_count": stats.detail_not_attempted_count,
        "cached_full_reused_count": stats.cached_full_reused_count,
        "failure_breakdown": dict(stats.failure_breakdown),
        "failure_breakdown_by_source": dict(stats.failure_breakdown_by_source),
        "source_detail_breakdown": dict(stats.source_detail_breakdown),
        "stage_durations_ms": dict(stats.stage_durations_ms),
        "effective_concurrency": dict(stats.effective_concurrency),
    }
    await db.commit()
    await db.refresh(execution)
    return execution, stats
