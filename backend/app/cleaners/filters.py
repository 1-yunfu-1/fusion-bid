"""二次条件过滤：时间、区域、关键词、公告相关性."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from app.parsers.regions import resolve_region_selection
from app.sources.base import DetailResult, ListItem

_TENDER_HINTS = ("招标", "采购", "投标", "中标", "询价", "磋商", "谈判", "比选", "邀标", "竞价")
_NOISE_HINTS = ("培训班", "广告投放", "招聘会", "政策解读专题", "意见征集无关")


@dataclass
class FilterContext:
    keywords: Sequence[str]
    regions: Sequence[str]
    start_date: date | None
    end_date: date | None


@dataclass
class FilterResult:
    accepted: bool
    reason: str = ""


def _in_date_range(pub: datetime | None, start: date | None, end: date | None) -> bool:
    if pub is None:
        # 无发布时间时放行到详情再判，列表阶段不硬杀
        return True
    d = pub.date() if isinstance(pub, datetime) else pub
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _region_match(text: str, regions: Sequence[str]) -> bool:
    effective_regions = resolve_region_selection(regions).effective
    if not effective_regions:
        return True
    if not text:
        return True  # 未知区域：二次在详情再严格
    for r in effective_regions:
        short = r.replace("省", "").replace("市", "").replace("自治区", "").replace("特别行政区", "")
        if r in text or (short and short in text):
            return True
    return False


def _keyword_match(text: str, keywords: Sequence[str]) -> bool:
    if not keywords:
        return True
    return any(k and k in text for k in keywords)


def is_tender_like(text: str) -> bool:
    if not text:
        return False
    if any(n in text for n in _NOISE_HINTS):
        return False
    return any(h in text for h in _TENDER_HINTS)


def filter_list_item(item: ListItem, ctx: FilterContext) -> FilterResult:
    blob = " ".join(filter(None, [item.title, item.snippet or "", item.region or ""]))
    if not is_tender_like(blob):
        return FilterResult(False, "非招标/采购类标题")
    if not _keyword_match(blob, ctx.keywords):
        return FilterResult(False, "关键词不匹配")
    if item.region and not _region_match(item.region + " " + item.title, ctx.regions):
        return FilterResult(False, "区域不匹配")
    if not _in_date_range(item.publish_time, ctx.start_date, ctx.end_date):
        return FilterResult(False, "发布时间不在范围内")
    return FilterResult(True)


def filter_detail(detail: DetailResult, ctx: FilterContext) -> FilterResult:
    blob = " ".join(
        filter(
            None,
            [detail.title, detail.region or "", detail.clean_content or detail.raw_content or ""],
        )
    )
    if not is_tender_like(detail.title + blob[:200]):
        return FilterResult(False, "详情非招标/采购类")
    if not _keyword_match(blob, ctx.keywords):
        return FilterResult(False, "详情关键词不匹配")
    # 区域：标题+正文+region 字段
    if ctx.regions and not _region_match(blob, ctx.regions):
        return FilterResult(False, "详情区域不匹配")
    if not _in_date_range(detail.publish_time, ctx.start_date, ctx.end_date):
        return FilterResult(False, "详情发布时间不在范围内")
    return FilterResult(True)


def simple_summary(title: str, clean_content: str, max_len: int = 600) -> str:
    """阶段三：基于原文的抽取式摘要，不调用 LLM 编造."""
    lines = [ln.strip() for ln in (clean_content or "").splitlines() if ln.strip()]
    keys = (
        "项目名称",
        "采购人",
        "招标人",
        "采购单位",
        "预算",
        "金额",
        "截止",
        "地点",
        "地区",
        "资格",
        "包件",
    )
    picked: list[str] = []
    for ln in lines:
        if any(k in ln for k in keys):
            picked.append(ln)
        if len(picked) >= 8:
            break
    if not picked:
        body = re.sub(r"\s+", " ", clean_content or "")[:max_len]
        picked = [body] if body else ["原文未明确说明"]
    head = f"标题：{title}" if title else ""
    text = "\n".join([head, *picked] if head else picked)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text
