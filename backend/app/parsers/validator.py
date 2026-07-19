"""意图严格校验：含糊与冲突不得静默编造."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.parsers.regions import resolve_region_selection
from app.schemas.intent import ParsedIntent, ValidationIssue


def validate_intent(
    intent: ParsedIntent,
    *,
    reference_time: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> list[ValidationIssue]:
    tz = ZoneInfo(timezone)
    now = reference_time or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    issues: list[ValidationIssue] = []

    if not intent.keywords:
        issues.append(
            ValidationIssue(
                code="missing_keywords",
                message="未识别到主题/关键词。请补充，例如：服务器、充电桩。",
                field="keywords",
                severity="error",
            )
        )

    region_selection = resolve_region_selection(intent.regions)
    if not region_selection.requested:
        issues.append(
            ValidationIssue(
                code="missing_regions",
                message="未识别到区域。请补充，例如：安徽省、上海市、北京市。",
                field="regions",
                severity="error",
            )
        )
    elif region_selection.had_conflict:
        issues.append(
            ValidationIssue(
                code="nationwide_overrides_regions",
                message="同时选择了全国和具体省市；已按全国范围处理，不进行地区过滤。",
                field="regions",
                severity="warning",
            )
        )

    dr = intent.date_range
    if dr.start_date and dr.end_date and dr.start_date > dr.end_date:
        issues.append(
            ValidationIssue(
                code="conflicting_dates",
                message=f"时间范围冲突：开始日期 {dr.start_date} 晚于结束日期 {dr.end_date}。",
                field="date_range",
                severity="error",
            )
        )
    elif not dr.start_date and not dr.end_date:
        issues.append(
            ValidationIssue(
                code="missing_date_range",
                message="未识别到明确时间范围。请指定，例如：最近1个月、2026年3月份。",
                field="date_range",
                severity="error",
            )
        )
    elif dr.start_date is None or dr.end_date is None:
        issues.append(
            ValidationIssue(
                code="incomplete_date_range",
                message="时间范围不完整，请同时确认开始与结束日期。",
                field="date_range",
                severity="error",
            )
        )

    sch = intent.schedule
    if sch.enabled:
        if not sch.execute_time:
            issues.append(
                ValidationIssue(
                    code="missing_execute_time",
                    message="已启用定时任务但未指定执行时间，请填写如 09:00。",
                    field="schedule.execute_time",
                    severity="error",
                )
            )
        if sch.schedule_type == "once" and sch.execute_time:
            exec_date = sch.execute_date or now.date()
            try:
                hh, mm = map(int, sch.execute_time.split(":")[:2])
                run_at = datetime(
                    exec_date.year,
                    exec_date.month,
                    exec_date.day,
                    hh,
                    mm,
                    tzinfo=tz,
                )
            except ValueError:
                issues.append(
                    ValidationIssue(
                        code="invalid_execute_time",
                        message=f"执行时间格式无效：{sch.execute_time}",
                        field="schedule.execute_time",
                        severity="error",
                    )
                )
            else:
                if run_at <= now:
                    issues.append(
                        ValidationIssue(
                            code="expired_schedule",
                            message=(
                                f"指定的执行时间 {exec_date.isoformat()} {sch.execute_time} 已过期，"
                                "不得静默创建过期任务。请选择立即执行，或改为明天/其他有效时间。"
                            ),
                            field="schedule",
                            severity="error",
                        )
                    )

        if sch.schedule_type not in (None, "once", "daily", "weekly", "monthly"):
            issues.append(
                ValidationIssue(
                    code="invalid_schedule_type",
                    message=f"不支持的调度类型：{sch.schedule_type}",
                    field="schedule.schedule_type",
                    severity="error",
                )
            )

    # 逻辑一致性提示（非阻断）
    if sch.enabled and intent.execute_immediately:
        issues.append(
            ValidationIssue(
                code="schedule_and_immediate",
                message="同时开启了定时与立即执行：确认后将按你的选择执行；默认定时任务不会在过期时刻静默运行。",
                field="execute_immediately",
                severity="warning",
            )
        )

    return issues


def suggestions_for(issues: list[ValidationIssue]) -> list[str]:
    tips: list[str] = []
    codes = {i.code for i in issues}
    if "missing_keywords" in codes:
        tips.append("在问题中写明采购主题，或在确认页手动填写关键词。")
    if "missing_regions" in codes:
        tips.append("写明省/市名称，或在确认页从区域列表选择。")
    if "missing_date_range" in codes or "incomplete_date_range" in codes:
        tips.append("使用「最近N个月」或「YYYY年M月份」等明确表达。")
    if "expired_schedule" in codes:
        tips.append("勾选「立即执行」，或把单次任务改到明天同一时间 / 每日定时。")
    if "conflicting_dates" in codes:
        tips.append("检查开始日期是否早于或等于结束日期。")
    return tips
