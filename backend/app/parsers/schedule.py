"""执行时间与频率解析."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass
class ScheduleResult:
    enabled: bool = False
    schedule_type: str | None = None  # once | daily | weekly | monthly
    execute_date: date | None = None
    execute_time: str | None = None  # HH:MM
    execute_immediately: bool = True
    expired: bool = False
    original_expression: str | None = None
    messages: list[str] | None = None


_TIME_RE = re.compile(
    r"(?:上午|下午|早上|晚上|中午)?"
    r"\s*"
    r"(?:(\d{1,2})\s*[点时:](?:\s*(\d{1,2})\s*分?)?|(\d{1,2}):(\d{2}))"
)

_DAILY = re.compile(r"(每天|每日|天天|按日)")
_WEEKLY = re.compile(r"(每周|按周|一周一)")
_MONTHLY = re.compile(r"(每月|按月)")
_ONCE_HINT = re.compile(r"(仅一次|只执行一次|单次|定时发送一次)")
_SEND_HINT = re.compile(r"(发送|推送|汇总后|发给我|通知我|提醒我)")
_TODAY = re.compile(r"(今天|今日)")
_TOMORROW = re.compile(r"(明天|明日)")
_DATE_YMD = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")


def _normalize_time(hour: int, minute: int, *, afternoon_hint: bool, text_slice: str) -> time | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if "下午" in text_slice or "晚上" in text_slice:
        if hour < 12:
            hour += 12
    elif "中午" in text_slice and hour < 12:
        hour = 12 if hour == 0 else hour
    elif afternoon_hint and hour < 12:
        hour += 12
    if hour > 23:
        return None
    return time(hour, minute)


def extract_time(text: str) -> tuple[str | None, str | None]:
    """返回 (HH:MM, matched_expression)."""
    m = _TIME_RE.search(text)
    if not m:
        return None, None
    slice_ = text[max(0, m.start() - 4) : m.end() + 2]
    if m.group(3) is not None:
        hour, minute = int(m.group(3)), int(m.group(4))
    else:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
    t = _normalize_time(hour, minute, afternoon_hint=False, text_slice=slice_)
    if not t:
        return None, None
    return f"{t.hour:02d}:{t.minute:02d}", m.group(0).strip()


def parse_schedule(text: str, *, now: datetime, timezone: str = "Asia/Shanghai") -> ScheduleResult:
    tz = ZoneInfo(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    time_str, time_expr = extract_time(text)
    messages: list[str] = []

    daily = _DAILY.search(text)
    weekly = _WEEKLY.search(text)
    monthly = _MONTHLY.search(text)
    once_hint = _ONCE_HINT.search(text)
    send_hint = _SEND_HINT.search(text)
    today_m = _TODAY.search(text)
    tomorrow_m = _TOMORROW.search(text)
    ymd = _DATE_YMD.search(text)

    # 仅“今天/明天 + 时间 + 发送类” 或 明确频率，才启用定时
    wants_schedule = bool(
        daily or weekly or monthly or once_hint or (send_hint and time_str and (today_m or tomorrow_m or ymd))
    )
    # “请汇总后每天9:00发送” — daily + time
    if daily or weekly or monthly:
        wants_schedule = True

    # “今天9:00发送给我” — once
    if send_hint and time_str and (today_m or tomorrow_m or ymd):
        wants_schedule = True

    # 仅有“发送给我”而无时间/频率 → 立即执行，不建定时
    if not wants_schedule:
        # 用户明确立即
        immediate = True
        if re.search(r"(立即|马上|现在)(执行|查询|搜索|汇总)?", text):
            immediate = True
        return ScheduleResult(enabled=False, execute_immediately=immediate)

    schedule_type = "once"
    execute_date: date | None = None
    expr_parts: list[str] = []

    if daily:
        schedule_type = "daily"
        expr_parts.append(daily.group(0))
    elif weekly:
        schedule_type = "weekly"
        expr_parts.append(weekly.group(0))
    elif monthly:
        schedule_type = "monthly"
        expr_parts.append(monthly.group(0))
    else:
        schedule_type = "once"
        if tomorrow_m:
            execute_date = (now + timedelta(days=1)).date()
            expr_parts.append(tomorrow_m.group(0))
        elif today_m:
            execute_date = now.date()
            expr_parts.append(today_m.group(0))
        elif ymd:
            execute_date = date(int(ymd.group(1)), int(ymd.group(2)), int(ymd.group(3)))
            expr_parts.append(ymd.group(0))
        else:
            execute_date = now.date()

    if time_expr:
        expr_parts.append(time_expr)

    expired = False
    if schedule_type == "once" and time_str and execute_date is not None:
        hh, mm = map(int, time_str.split(":"))
        run_at = datetime(
            execute_date.year,
            execute_date.month,
            execute_date.day,
            hh,
            mm,
            tzinfo=tz,
        )
        if run_at <= now:
            expired = True
            messages.append(
                f"指定的执行时间 {execute_date.isoformat()} {time_str} 已过期。"
                "请选择立即执行，或改为明天/其他有效时间。"
            )

    # 有定时且未过期 → 默认不立即执行；过期则仍标记 enabled 供用户修正，但不静默执行过期任务
    execute_immediately = False
    if expired:
        execute_immediately = False

    return ScheduleResult(
        enabled=True,
        schedule_type=schedule_type,
        execute_date=execute_date if schedule_type == "once" else None,
        execute_time=time_str or "09:00",
        execute_immediately=execute_immediately,
        expired=expired,
        original_expression="".join(expr_parts) or None,
        messages=messages or None,
    )
