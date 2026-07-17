"""规则意图解析（LLM 降级方案）."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.parsers.dates import parse_date_range
from app.parsers.keywords import extract_exclude_keywords, extract_keywords
from app.parsers.regions import extract_regions
from app.parsers.schedule import parse_schedule
from app.schemas.intent import DateRangeSchema, ParsedIntent, ScheduleSchema


def parse_intent_by_rules(
    query: str,
    *,
    reference_time: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> ParsedIntent:
    tz = ZoneInfo(timezone)
    now = reference_time or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    regions = extract_regions(query)
    keywords = extract_keywords(query)
    exclude = extract_exclude_keywords(query)
    dr = parse_date_range(query, now=now, timezone=timezone)
    sch = parse_schedule(query, now=now, timezone=timezone)

    schedule = ScheduleSchema(
        enabled=sch.enabled,
        schedule_type=sch.schedule_type,  # type: ignore[arg-type]
        execute_date=sch.execute_date,
        execute_time=sch.execute_time,
        timezone=timezone,
    )

    execute_immediately = sch.execute_immediately
    if not sch.enabled:
        execute_immediately = True

    return ParsedIntent(
        original_query=query,
        keywords=keywords,
        exclude_keywords=exclude,
        regions=regions,
        date_range=DateRangeSchema(
            start_date=dr.start_date,
            end_date=dr.end_date,
            original_expression=dr.original_expression,
        ),
        schedule=schedule,
        execute_immediately=execute_immediately,
    )
