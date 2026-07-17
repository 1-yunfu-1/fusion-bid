"""多源采集编排：检索 → 详情 → 清洗 → 过滤 → 跨源去重 → 增量 → 入库."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.session import LoginRequiredError
from app.cleaners.filters import FilterContext, filter_detail, filter_list_item, simple_summary
from app.core.config import get_settings
from app.deduplication.engine import CandidateRecord, deduplicate_candidates
from app.deduplication.incremental import mark_delivered, plan_incremental_delivery
from app.deduplication.normalize import normalize_bid_code
from app.models.announcement import TenderAnnouncement
from app.models.execution import TaskExecution
from app.models.task import SearchTask
from app.reports.fields import source_display_name
from app.reports.word_report import ReportContext, SourceRunStat, generate_report_file
from app.sources.base import SearchQuery
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
    detail_success_count: int = 0
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


def _content_hash(title: str, content: str, url: str) -> str:
    raw = f"{title}|{content[:2000]}|{url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_dedupe_key(source_name: str, item_id: str | None, url: str, title: str) -> str:
    if item_id:
        return f"{source_name}:{item_id}"
    return f"{source_name}:{hashlib.md5((url or title).encode()).hexdigest()}"


async def execute_search_task(
    db: AsyncSession,
    task: SearchTask,
    *,
    max_details_per_source: int = 8,
) -> tuple[TaskExecution, CrawlStats]:
    get_settings()
    stats = CrawlStats()
    now = datetime.now(TZ)

    execution = TaskExecution(
        task_id=task.id,
        started_at=now,
        status="running",
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

    for source in sources:
        src_stat = SourceRunStat(
            source_name=source.source_name,
            display_name=getattr(source, "display_name", None)
            or source_display_name(source.source_name),
        )
        try:
            if source.requires_login:
                health = await source.health_check()
                if not health.ok or health.login_ok is False:
                    msg = health.message or "登录态不可用，已跳过（公开源继续）"
                    stats.sources_failed[source.source_name] = msg
                    src_stat.status = "skipped"
                    src_stat.message = msg
                    stats.source_stats.append(src_stat)
                    continue
            list_items = await source.search(query)
            src_stat.raw_count = len(list_items)
            stats.raw_result_count += len(list_items)
            kept = []
            for it in list_items:
                fr = filter_list_item(it, ctx)
                if fr.accepted:
                    kept.append(it)
                else:
                    stats.list_filtered_out += 1
                    stats.filtered_out_count += 1

            src_stat.list_kept = len(kept)
            if len(kept) > max_details_per_source:
                stats.detail_cap_skipped += len(kept) - max_details_per_source

            for it in kept[:max_details_per_source]:
                att_extract_failed = False
                try:
                    detail = await source.fetch_detail(it)
                    try:
                        atts = await source.extract_attachments(detail)
                        detail.attachment_links = atts or []
                    except Exception as att_exc:  # noqa: BLE001
                        logger.warning(
                            "%s attach extract fail %s: %s",
                            source.source_name,
                            it.source_url,
                            att_exc,
                        )
                        detail.attachment_links = list(detail.attachment_links or [])
                        att_extract_failed = True
                    stats.detail_success_count += 1
                    src_stat.detail_success += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s detail fail %s: %s", source.source_name, it.source_url, exc)
                    stats.detail_failed += 1
                    detail_meta[it.source_url] = {
                        "detail_fetched": False,
                        "attachment_extract_failed": False,
                    }
                    continue

                dfr = filter_detail(detail, ctx)
                if not dfr.accepted:
                    stats.detail_filtered_out += 1
                    stats.filtered_out_count += 1
                    continue

                dkey = _source_dedupe_key(
                    source.source_name, it.source_item_id, detail.source_url, detail.title
                )
                chash = _content_hash(detail.title, detail.clean_content, detail.source_url)
                summary = simple_summary(detail.title, detail.clean_content)
                code = normalize_bid_code(f"{detail.title} {detail.clean_content or ''}")
                detail_meta[detail.source_url] = {
                    "detail_fetched": True,
                    "attachment_extract_failed": att_extract_failed,
                    "requires_login": source.requires_login,
                }

                candidates.append(
                    CandidateRecord(
                        title=detail.title[:512],
                        source_name=source.source_name,
                        source_url=detail.source_url,
                        source_item_id=it.source_item_id,
                        requires_login=source.requires_login,
                        data_mode="live",
                        publish_time=detail.publish_time,
                        region=detail.region or it.region,
                        province=None,
                        keywords=keywords,
                        summary=summary,
                        clean_content=detail.clean_content,
                        raw_content=(detail.raw_content or "")[:500000],
                        attachment_links=list(detail.attachment_links or []),
                        content_hash=chash,
                        deduplication_key=dkey,
                        project_code=code,
                    )
                )

            stats.sources_succeeded.append(source.source_name)
            src_stat.status = "success"
            src_stat.message = "检索完成"
            stats.source_stats.append(src_stat)
        except LoginRequiredError as exc:
            msg = str(exc)
            logger.warning("login source %s skipped: %s", source.source_name, msg)
            stats.sources_failed[source.source_name] = msg
            src_stat.status = "skipped"
            src_stat.message = msg
            stats.source_stats.append(src_stat)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.exception("source %s failed", source.source_name)
            stats.sources_failed[source.source_name] = msg
            src_stat.status = "failed"
            src_stat.message = msg
            stats.source_stats.append(src_stat)
            errors.append(f"{source.source_name}: {msg}")

    # ---- 跨源去重（本批次）----
    stats.candidates_count = len(candidates)
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
        if rec.project_code:
            existing = await db.scalar(
                select(TenderAnnouncement).where(
                    TenderAnnouncement.project_code == rec.project_code,
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
            # 内容更完整则更新
            if rec.clean_content and len(rec.clean_content) > len(existing.clean_content or ""):
                existing.clean_content = rec.clean_content
                existing.summary = rec.summary
                existing.raw_content = rec.raw_content
            atts = list(existing.attachment_links or [])
            for a in rec.attachment_links or []:
                if a not in atts:
                    atts.append(a)
            existing.attachment_links = atts
            existing.content_hash = rec.content_hash or existing.content_hash
            if rec.project_code and not existing.project_code:
                existing.project_code = rec.project_code
            ann_id = existing.id
            chash = existing.content_hash or rec.content_hash or ""
        else:
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
                attachment_links=rec.attachment_links or [],
                crawl_time=crawl_time,
                content_hash=rec.content_hash,
                deduplication_key=rec.deduplication_key,
                is_primary=True,
                related_urls=rec.related_urls or [rec.source_url],
                related_sources=rec.related_sources or [],
                dedupe_reasons=rec.dedupe_reasons or [],
                project_code=rec.project_code,
                first_seen_at=crawl_time,
                last_seen_at=crawl_time,
            )
            db.add(ann)
            await db.flush()
            stats.saved_count += 1
            ann_id = ann.id
            chash = rec.content_hash or ""

        saved_ids.append(ann_id)
        id_hash_pairs.append((ann_id, chash))

    # ---- 增量：相对本任务 DeliveryHistory ----
    plan = await plan_incremental_delivery(db, task_id=task.id, announcements=id_hash_pairs)
    stats.incremental_count = plan.new_count
    stats.update_count = plan.update_count
    stats.skipped_already_delivered = plan.skipped_count
    stats.announcement_ids = saved_ids

    output_items: list[dict] = []
    for item in plan.items:
        ann = await db.get(TenderAnnouncement, item.announcement_id)
        if not ann:
            continue
        meta = detail_meta.get(ann.source_url) or {
            "detail_fetched": True,
            "attachment_extract_failed": False,
            "requires_login": ann.requires_login,
        }
        output_items.append(
            {
                "announcement_id": ann.id,
                "title": ann.title,
                "source_name": ann.source_name,
                "source_url": ann.source_url,
                "related_urls": ann.related_urls or [ann.source_url],
                "related_sources": ann.related_sources or [],
                "dedupe_reasons": ann.dedupe_reasons or [],
                "is_new": item.is_new,
                "is_update": item.is_update,
                "change_label": "内容发生变化" if item.is_update else ("新增" if item.is_new else ""),
                "content_hash": item.content_hash,
                "summary": ann.summary,
                "clean_content": (ann.clean_content or "")[:8000],
                "publish_time": ann.publish_time.isoformat() if ann.publish_time else None,
                "region": ann.region,
                "project_code": ann.project_code,
                "attachment_links": ann.attachment_links or [],
                "data_mode": ann.data_mode,
                "requires_login": ann.requires_login,
                "detail_fetched": meta.get("detail_fetched", True),
                "attachment_extract_failed": meta.get("attachment_extract_failed", False),
            }
        )
    stats.output_items = output_items

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

    if stats.sources_succeeded:
        execution.status = "partial" if stats.sources_failed else "success"
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

    report_ctx = ReportContext(
        original_query=task.original_query,
        generated_at=execution.finished_at or datetime.now(TZ),
        execute_type="定时执行" if task.schedule_enabled and not task.execute_immediately else "立即执行",
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

    task.status = "done" if execution.status in ("success", "partial") else "failed"
    await db.commit()
    await db.refresh(execution)
    return execution, stats
