"""时间范围解析：相对区间、指定年月、灵活月份表达."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser


@dataclass
class DateRangeResult:
    start_date: date | None
    end_date: date | None
    original_expression: str | None
    ambiguous: bool = False
    conflict: bool = False
    messages: list[str] | None = None


_RELATIVE_RE = re.compile(
    r"(最近|近|过去)\s*(\d+)\s*(年|个月|月|周|星期|天|日)"
)
_YEAR_MONTH_RE = re.compile(
    r"((?:20\d{2})\s*年)?\s*(\d{1,2})\s*月(?:份|份份)?"
)
_YEAR_ONLY_RE = re.compile(r"(20\d{2})\s*年(?!\s*\d{1,2}\s*月)")
_EXPLICIT_RANGE_RE = re.compile(
    r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})\s*[至到~\-—]\s*"
    r"(?:(20\d{2})[年\-/.])?(\d{1,2})[月\-/.](\d{1,2})"
)
_CN_MONTH = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def _parse_cn_month_token(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        m = int(token)
        return m if 1 <= m <= 12 else None
    return _CN_MONTH.get(token)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def parse_date_range(text: str, *, now: datetime, timezone: str = "Asia/Shanghai") -> DateRangeResult:
    """解析时间范围；不得编造冲突结论，保留 original_expression."""
    tz = ZoneInfo(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    today = now.date()
    messages: list[str] = []

    # 1) 显式区间
    m = _EXPLICIT_RANGE_RE.search(text)
    if m:
        y1, mo1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y2 = int(m.group(4)) if m.group(4) else y1
        mo2, d2 = int(m.group(5)), int(m.group(6))
        try:
            start = date(y1, mo1, d1)
            end = date(y2, mo2, d2)
        except ValueError:
            return DateRangeResult(None, None, m.group(0), conflict=True, messages=["日期格式非法"])
        if start > end:
            return DateRangeResult(
                start,
                end,
                m.group(0),
                conflict=True,
                messages=["开始日期晚于结束日期"],
            )
        return DateRangeResult(start, end, m.group(0).strip())

    # 2) 最近 N 单位
    m = _RELATIVE_RE.search(text)
    if m:
        n = int(m.group(2))
        unit = m.group(3)
        end = today
        if unit == "年":
            try:
                start = end.replace(year=end.year - n)
            except ValueError:
                start = date(end.year - n, end.month, 28)
        elif unit in ("个月", "月"):
            # 近似：按天数 30*n，同时用 month 回退保证边界
            month = end.month - n
            year = end.year
            while month <= 0:
                month += 12
                year -= 1
            day = min(end.day, calendar.monthrange(year, month)[1])
            start = date(year, month, day)
        elif unit in ("周", "星期"):
            start = end - timedelta(days=7 * n)
        else:
            start = end - timedelta(days=n)
        return DateRangeResult(start, end, m.group(0).strip())

    # 3) 中文月份：四月份 / 4月份 / 2026年3月份
    # 优先匹配带年份
    ym = re.search(r"(20\d{2})\s*年\s*(\d{1,2}|[一二三四五六七八九十]+)\s*月(?:份)?", text)
    if ym:
        year = int(ym.group(1))
        month = _parse_cn_month_token(ym.group(2))
        if month:
            start, end = _month_bounds(year, month)
            return DateRangeResult(start, end, ym.group(0).strip())

    # 无年份月份：默认当前年份（赛题：转为明确日期）
    bare = re.search(r"(?<!\d)(\d{1,2}|[一二三四五六七八九十]+)\s*月(?:份)?(?!\s*\d)", text)
    if bare and "个月" not in text[max(0, bare.start() - 1) : bare.end() + 1]:
        # 排除“最近3个月”已处理；此处 bare 可能误伤，再检查上下文
        ctx = text[max(0, bare.start() - 3) : bare.end()]
        if "最近" in ctx or "近" in ctx or "过去" in ctx:
            pass
        else:
            month = _parse_cn_month_token(bare.group(1))
            if month:
                year = today.year
                start, end = _month_bounds(year, month)
                expr = bare.group(0).strip()
                # 若该月完全在未来且跨年语义不明确，仍用当前年并提示
                if start > today:
                    messages.append(
                        f"识别到「{expr}」，已按 {year} 年解释为 {start} 至 {end}；如需其他年份请修改。"
                    )
                return DateRangeResult(
                    start,
                    end,
                    expr,
                    ambiguous=start > today,
                    messages=messages or None,
                )

    # 4) 仅年份
    yo = _YEAR_ONLY_RE.search(text)
    if yo:
        year = int(yo.group(1))
        return DateRangeResult(date(year, 1, 1), date(year, 12, 31), yo.group(0).strip())

    # 5) dateparser 兜底：尝试“本月/上月”
    for phrase in ("本月", "这个月", "上月", "上个月", "本周", "今天", "昨日", "昨天"):
        if phrase in text:
            settings = {
                "PREFER_DATES_FROM": "past",
                "TIMEZONE": timezone,
                "RETURN_AS_TIMEZONE_AWARE": True,
            }
            if phrase in ("本月", "这个月"):
                start, end = _month_bounds(today.year, today.month)
                return DateRangeResult(start, min(end, today), phrase)
            if phrase in ("上月", "上个月"):
                month = today.month - 1 or 12
                year = today.year if today.month > 1 else today.year - 1
                start, end = _month_bounds(year, month)
                return DateRangeResult(start, end, phrase)
            if phrase == "本周":
                start = today - timedelta(days=today.weekday())
                return DateRangeResult(start, today, phrase)
            if phrase == "今天":
                return DateRangeResult(today, today, phrase)
            if phrase in ("昨日", "昨天"):
                d = today - timedelta(days=1)
                return DateRangeResult(d, d, phrase)
            _ = dateparser  # 保留依赖引用，复杂句后续扩展

    return DateRangeResult(None, None, None, ambiguous=True, messages=["未识别到明确时间范围"])
