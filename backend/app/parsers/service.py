"""意图解析编排：API → Ollama → 规则 + 校验."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.llm_runtime import effective_llm_settings
from app.llm.client import parse_intent_llm_chain
from app.parsers.regions import resolve_region_selection
from app.parsers.rule_parser import parse_intent_by_rules
from app.parsers.validator import suggestions_for, validate_intent
from app.schemas.intent import ParseResponse, ParsedIntent


async def parse_user_query(
    query: str,
    *,
    reference_time: datetime | None = None,
    prefer_llm: bool | None = None,
) -> ParseResponse:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone)
    now = reference_time or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    query = query.strip()
    eff = effective_llm_settings()
    order = list(eff["prefer_order"])

    llm_attempted = False
    llm_success = False
    llm_error: str | None = None
    parser_used: str = "rule"
    intent: ParsedIntent | None = None
    model_used: str | None = None
    provider_used: str | None = None

    use_llm = prefer_llm if prefer_llm is not None else True
    llm_providers = [x for x in order if x in ("api", "ollama")]

    if use_llm and llm_providers:
        llm_attempted = True
        result = await parse_intent_llm_chain(query, reference_time=now, prefer_order=llm_providers)
        if result.success and result.intent:
            intent = result.intent
            llm_success = True
            parser_used = result.provider or "llm"
            model_used = result.model
            provider_used = result.provider
        else:
            llm_error = result.error

    if intent is None:
        intent = parse_intent_by_rules(query, reference_time=now, timezone=settings.app_timezone)
        parser_used = "rule" if not llm_success else parser_used
        if llm_attempted and not llm_success:
            parser_used = "rule"

    # hybrid：若 LLM 缺字段，用规则补全空缺（不覆盖已有）
    if llm_success and intent is not None:
        rule = parse_intent_by_rules(query, reference_time=now, timezone=settings.app_timezone)
        merged = False
        if not intent.keywords and rule.keywords:
            intent.keywords = rule.keywords
            merged = True
        if not intent.regions and rule.regions:
            intent.regions = rule.regions
            merged = True
        if (
            intent.date_range.start_date is None
            and rule.date_range.start_date is not None
        ):
            intent.date_range = rule.date_range
            merged = True
        if not intent.schedule.enabled and rule.schedule.enabled:
            intent.schedule = rule.schedule
            intent.execute_immediately = rule.execute_immediately
            merged = True
        if merged and parser_used in ("api", "ollama"):
            parser_used = "hybrid"

    region_selection = resolve_region_selection(intent.regions)
    issues = validate_intent(intent, reference_time=now, timezone=settings.app_timezone)
    intent.regions = region_selection.requested
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i.message for i in issues if i.severity == "warning"]
    tips = suggestions_for(issues)

    # 扩展 warnings：解析通道信息
    channel_notes: list[str] = []
    if llm_attempted and llm_success:
        channel_notes.append(f"大模型解析成功（{provider_used}/{model_used}）")
    elif llm_attempted and not llm_success:
        channel_notes.append(f"大模型不可用已降级规则解析：{llm_error}")
    else:
        channel_notes.append("未启用大模型，使用规则解析")
    if region_selection.scope == "nationwide":
        channel_notes.append("已识别为全国范围，不进行地区过滤")

    return ParseResponse(
        intent=intent,
        parser_used=parser_used,  # type: ignore[arg-type]
        llm_attempted=llm_attempted,
        llm_success=llm_success,
        llm_error=llm_error,
        issues=issues,
        needs_user_input=len(errors) > 0,
        can_confirm=True,  # 允许用户改完再确认；真正落库时再验
        suggestions=tips,
        warnings=warnings + channel_notes,
    )
