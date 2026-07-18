"""跨来源去重引擎：合并重复公告，保留主记录."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.deduplication.normalize import (
    attachment_name_set,
    normalize_bid_code,
    normalize_title,
)
from app.deduplication.similarity import content_similarity, title_similarity

# 官方源优先（数值越小优先级越高）
SOURCE_PRIORITY: dict[str, int] = {
    "ccgp": 0,
    "cebpub": 1,
    "login_portal": 5,
    "mock_public": 9,
}


@dataclass
class CandidateRecord:
    """待去重的中间记录（尚未入库或已入库）."""

    title: str
    source_name: str
    source_url: str
    source_item_id: str | None = None
    requires_login: bool = False
    data_mode: str = "live"
    publish_time: datetime | None = None
    region: str | None = None
    province: str | None = None
    keywords: list[str] | None = None
    summary: str | None = None
    clean_content: str | None = None
    raw_content: str | None = None
    detail_status: str = "unknown"
    source_metadata: dict[str, Any] | None = None
    extraction_data: dict[str, Any] | None = None
    attachment_links: list[str] = field(default_factory=list)
    content_hash: str | None = None
    deduplication_key: str | None = None
    project_code: str | None = None
    announcement_type: str | None = None
    publisher: str | None = None
    # 入库后填充
    db_id: str | None = None
    # 合并信息
    related_urls: list[str] = field(default_factory=list)
    related_sources: list[dict[str, Any]] = field(default_factory=list)
    dedupe_reasons: list[str] = field(default_factory=list)
    is_primary: bool = True

    def completeness_score(self) -> int:
        score = 0
        score += min(len(self.clean_content or ""), 5000) // 50
        score += len(self.attachment_links or []) * 20
        if self.publish_time:
            score += 15
        if self.region:
            score += 10
        if self.project_code or normalize_bid_code(
            (self.clean_content or "") + " " + (self.title or "")
        ):
            score += 25
        if self.detail_status == "full":
            score += 80
        elif self.detail_status == "metadata_only":
            score += 5
        # 官方加权
        score += max(0, 30 - SOURCE_PRIORITY.get(self.source_name, 10) * 5)
        return score


@dataclass
class DedupeResult:
    primaries: list[CandidateRecord]
    merged_count: int
    reasons: list[str] = field(default_factory=list)


def _same_day(a: datetime | None, b: datetime | None) -> bool:
    if not a or not b:
        return True  # 缺时间不因时间否决
    return a.date() == b.date()


def _region_compatible(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    sa = a.replace("省", "").replace("市", "")
    sb = b.replace("省", "").replace("市", "")
    return sa in b or sb in a or sa in sb or sb in sa


def announcement_lifecycle_type(record: CandidateRecord) -> str:
    """识别公告生命周期节点，用于防止原公告被终止/更正公告覆盖。"""
    explicit = (record.announcement_type or "").strip()
    probe = f"{explicit} {record.title}"
    rules = (
        ("终止公告", ("终止公告", "终止招标", "招标终止")),
        ("废标公告", ("废标公告", "流标公告", "采购失败")),
        ("更正公告", ("更正公告", "变更公告", "澄清公告")),
        ("中标公告", ("中标公告", "中标候选人公示", "中标结果")),
        ("成交公告", ("成交公告", "成交结果")),
        ("资格预审", ("资格预审",)),
        ("招标公告", ("招标公告", "采购公告", "磋商公告", "询价公告")),
    )
    for lifecycle, markers in rules:
        if any(marker in probe for marker in markers):
            return lifecycle
    return explicit or "未知"


def is_duplicate(left: CandidateRecord, right: CandidateRecord) -> tuple[bool, str]:
    """判断两条是否重复，返回 (是否重复, 依据)."""
    # 1) 同源 source_item_id
    if (
        left.source_name == right.source_name
        and left.source_item_id
        and left.source_item_id == right.source_item_id
    ):
        return True, "同源公告编号"

    # 2) 完全相同 URL
    if left.source_url and left.source_url == right.source_url:
        return True, "相同URL"

    # 3) 项目编号
    code_l = left.project_code or normalize_bid_code(
        f"{left.title} {left.clean_content or ''}"
    )
    code_r = right.project_code or normalize_bid_code(
        f"{right.title} {right.clean_content or ''}"
    )
    type_l = announcement_lifecycle_type(left)
    type_r = announcement_lifecycle_type(right)
    if code_l and code_r and code_l != code_r:
        return False, ""
    if code_l and code_r and code_l == code_r:
        if type_l != type_r and "未知" not in {type_l, type_r}:
            return False, ""
        return True, f"项目编号一致:{code_l}"

    # 4) 标题标准化完全一致 + 区域兼容 + 时间兼容
    nt_l, nt_r = normalize_title(left.title), normalize_title(right.title)
    if (
        nt_l
        and nt_l == nt_r
        and type_l == type_r
        and _region_compatible(left.region, right.region)
    ):
        if _same_day(left.publish_time, right.publish_time):
            return True, "标准化标题+区域+日期"

    # 5) 标题高相似 + 区域 + 正文相似或附件名重叠
    ts = title_similarity(left.title, right.title)
    if (
        ts >= 0.88
        and type_l == type_r
        and _region_compatible(left.region, right.region)
    ):
        cs = content_similarity(left.clean_content or "", right.clean_content or "")
        att_l = attachment_name_set(left.attachment_links)
        att_r = attachment_name_set(right.attachment_links)
        att_overlap = bool(att_l & att_r) if att_l and att_r else False
        # 标题相似本身不再足以合并；必须有高度正文或附件证据。
        if cs >= 0.75 or att_overlap:
            reason = f"标题相似({ts:.2f})"
            if cs >= 0.55:
                reason += f"+正文相似({cs:.2f})"
            if att_overlap:
                reason += "+附件名重叠"
            return True, reason

    # 6) 正文哈希
    if (
        left.detail_status == "full"
        and right.detail_status == "full"
        and type_l == type_r
        and left.content_hash
        and right.content_hash
        and left.content_hash == right.content_hash
    ):
        return True, "正文哈希一致"

    return False, ""


def choose_primary(a: CandidateRecord, b: CandidateRecord) -> CandidateRecord:
    """优先官方源、信息更完整、附件更多、发布时间更可信."""
    sa, sb = a.completeness_score(), b.completeness_score()
    if sa != sb:
        return a if sa > sb else b
    pa = SOURCE_PRIORITY.get(a.source_name, 50)
    pb = SOURCE_PRIORITY.get(b.source_name, 50)
    if pa != pb:
        return a if pa < pb else b
    # 发布时间更早或存在
    if a.publish_time and not b.publish_time:
        return a
    if b.publish_time and not a.publish_time:
        return b
    return a


def merge_into_primary(primary: CandidateRecord, other: CandidateRecord, reason: str) -> None:
    """将被合并来源信息并入主记录（不删除 other 的追溯信息）."""
    if other.source_url and other.source_url not in primary.related_urls:
        primary.related_urls.append(other.source_url)
    if primary.source_url and primary.source_url not in primary.related_urls:
        primary.related_urls.insert(0, primary.source_url)

    primary.related_sources.append(
        {
            "source_name": other.source_name,
            "source_url": other.source_url,
            "source_item_id": other.source_item_id,
            "title": other.title,
            "reason": reason,
        }
    )
    primary.dedupe_reasons.append(reason)

    # 补全主记录信息
    other_is_better = other.completeness_score() > primary.completeness_score()
    detail_is_better = other.detail_status == "full" and primary.detail_status != "full"
    same_detail_quality = other.detail_status == primary.detail_status
    if other.clean_content and (
        not primary.clean_content
        or detail_is_better
        or (same_detail_quality and len(other.clean_content) > len(primary.clean_content))
        or (same_detail_quality and other_is_better)
    ):
        primary.clean_content = other.clean_content
        primary.raw_content = other.raw_content
        primary.summary = other.summary
        primary.detail_status = other.detail_status
        primary.source_metadata = other.source_metadata
        primary.extraction_data = other.extraction_data
    # 附件并集
    seen = set(primary.attachment_links or [])
    for link in other.attachment_links or []:
        if link not in seen:
            primary.attachment_links.append(link)
            seen.add(link)
    if not primary.region and other.region:
        primary.region = other.region
    if not primary.publish_time and other.publish_time:
        primary.publish_time = other.publish_time
    if not primary.project_code:
        primary.project_code = other.project_code or normalize_bid_code(
            f"{other.title} {other.clean_content or ''}"
        )


def deduplicate_candidates(records: list[CandidateRecord]) -> DedupeResult:
    """对一批候选记录做跨源去重，返回主记录列表."""
    if not records:
        return DedupeResult(primaries=[], merged_count=0)

    # 预填 project_code
    for r in records:
        if not r.project_code:
            r.project_code = normalize_bid_code(f"{r.title} {r.clean_content or ''}")
        if r.source_url and r.source_url not in r.related_urls:
            r.related_urls = [r.source_url]

    primaries: list[CandidateRecord] = []
    merged = 0
    reasons: list[str] = []

    for rec in records:
        matched_idx: int | None = None
        match_reason = ""
        for i, p in enumerate(primaries):
            ok, reason = is_duplicate(p, rec)
            if ok:
                matched_idx = i
                match_reason = reason
                break
        if matched_idx is None:
            rec.is_primary = True
            primaries.append(rec)
            continue

        primary = primaries[matched_idx]
        winner = choose_primary(primary, rec)
        if winner is rec:
            # rec 成为新主记录
            merge_into_primary(rec, primary, match_reason)
            rec.is_primary = True
            primary.is_primary = False
            primaries[matched_idx] = rec
        else:
            merge_into_primary(primary, rec, match_reason)
            rec.is_primary = False
        merged += 1
        reasons.append(f"{rec.title[:40]} <- {match_reason}")

    return DedupeResult(primaries=primaries, merged_count=merged, reasons=reasons)
