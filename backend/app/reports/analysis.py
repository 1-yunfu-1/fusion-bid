"""Evidence-bounded tender opportunity analysis.

Rules are always available.  An optional LLM can add a short note only after
the note is tied to fields that were actually extracted from the announcement.
This module deliberately does not attempt bid-win prediction.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from app.llm.client import call_json_llm_chain
from app.llm.prompts import REPORT_ANALYSIS_SYSTEM_PROMPT, build_report_analysis_prompt
from app.reports.fields import _MISSING, enrich_report_item

TZ = ZoneInfo("Asia/Shanghai")
_MISSING_VALUES = {
    _MISSING,
    "本次未成功提取",
    "详情未获取，无法提取",
    "",
    None,
}


def _as_date(value: Any) -> date | None:
    if not value or value in _MISSING_VALUES:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.search(r"(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})", str(value))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _deadline_urgency(value: Any, *, today: date) -> tuple[str, str]:
    deadline = _as_date(value)
    if not deadline:
        return "未知", "公告原文未提取到可解析的投标/报名截止日期。"
    days = (deadline - today).days
    if days < 0:
        return "已过期", f"原文截止日期为 {deadline.isoformat()}，需先核对是否仍有效。"
    if days <= 3:
        return "紧急", f"距原文截止日期约 {days} 天，应优先核验并决定是否立项。"
    if days <= 7:
        return "一周内", f"距原文截止日期约 {days} 天，建议本周完成资格与资源评估。"
    if days <= 14:
        return "两周内", f"距原文截止日期约 {days} 天，可同步准备资格和方案材料。"
    return "时间相对充足", f"距原文截止日期约 {days} 天，仍应以公告原文及后续澄清为准。"


def _profile_text(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    parts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif value not in (None, ""):
            parts.append(str(value))

    visit(profile)
    return "\n".join(parts)


def _qualification_matrix(
    requirements: list[str], profile: dict[str, Any] | None
) -> list[dict[str, Any]]:
    profile_blob = _profile_text(profile)
    matrix: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements, 1):
        if not profile:
            status = "待企业画像"
            basis = "未配置企业画像，仅保留公告条款供人工核对。"
        else:
            # 只对企业画像中明确出现的资质/能力做正向匹配，不从公司名称推断。
            tokens = [
                token
                for token in re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,8}", requirement)
                if token
                not in {
                    "投标人",
                    "本次招标",
                    "应当",
                    "必须",
                    "要求",
                    "具备",
                    "提供",
                }
            ]
            hits = [token for token in tokens if token in profile_blob]
            if hits:
                status = "可匹配，待核验有效期"
                basis = f"企业画像命中：{'、'.join(hits[:5])}"
            else:
                status = "待核验"
                basis = "企业画像未找到可直接证明该条款的材料。"
        matrix.append(
            {
                "clause_id": f"Q{index}",
                "requirement": requirement,
                "status": status,
                "profile_basis": basis,
            }
        )
    return matrix


def _risk_materials(fields: dict[str, Any]) -> tuple[list[str], list[str]]:
    risks: list[str] = []
    materials: list[str] = []
    qualification = "\n".join(fields.get("qualification_items") or [])
    if "营业执照" in qualification or "法人" in qualification:
        materials.append("营业执照/法人资格证明")
    if "业绩" in qualification or "合同" in qualification:
        materials.append("同类项目合同及验收/业绩证明")
    if "信用" in qualification or "失信" in qualification:
        materials.append("信用查询及无失信证明")
    if fields.get("joint_venture_allowed") == "不允许":
        risks.append("不接受联合体，需确认能否独立满足全部资格与交付要求。")
    if fields.get("agent_allowed") == "允许":
        risks.append("允许代理商参与，仍需核对原厂授权、售后和供货责任。")
        materials.append("原厂授权与售后服务承诺（如适用）")
    if fields.get("platform_registration_required") == "需要":
        risks.append("需提前完成交易平台注册。")
    if fields.get("ca_required") == "需要":
        risks.append("需预留 CA 数字证书办理和电子签章时间。")
        materials.append("CA 数字证书/电子签章")
    return list(dict.fromkeys(risks)), list(dict.fromkeys(materials))


def _project_rule(
    item: dict[str, Any], *, today: date, company_profile: dict[str, Any] | None = None
) -> dict[str, Any]:
    fields = item.get("fields") or {}
    evidence = item.get("field_evidence") or {}
    detail_status = item.get("detail_status") or (
        "full" if item.get("detail_fetched") else "metadata_only"
    )
    deadline_state, deadline_note = _deadline_urgency(fields.get("deadline"), today=today)
    qualification_items = [
        x for x in (fields.get("qualification_items") or []) if x and x not in _MISSING_VALUES
    ]
    gaps: list[str] = []
    if detail_status != "full":
        gaps.append("尚未获得可验证的公告详情，仅有列表元数据")
    if fields.get("purchaser") in _MISSING_VALUES:
        gaps.append("招标人/采购人原文未提取")
    if not qualification_items and fields.get("qualification") in _MISSING_VALUES:
        gaps.append("资格要求原文未提取")
    if fields.get("deadline") in _MISSING_VALUES:
        gaps.append("投标或报名截止时间原文未提取")

    announcement_type = str(fields.get("announcement_type") or "")
    reasons: list[str] = []
    if announcement_type in {"中标公告", "成交公告", "废标公告"}:
        priority = "低"
        reasons.append("公告类型显示为结果/废标类，通常不作为新的投标机会")
    elif deadline_state == "已过期":
        priority = "低"
        reasons.append("已提取到可能过期的截止日期，需先人工核验")
    elif detail_status != "full":
        priority = "待核验"
        reasons.append("未获得可验证详情，不能据此判断资格与可投性")
    elif deadline_state in {"紧急", "一周内"}:
        priority = "高"
        reasons.append("时间窗口较短，适合优先做 Go/No-Go 核验")
    elif qualification_items:
        priority = "中"
        reasons.append("已提取资格要求，可发起资质符合性初审")
    else:
        priority = "待核验"
        reasons.append("详情存在但资格要求未提取完整")

    actions: list[str] = []
    if detail_status != "full":
        actions.append("打开官方详情页，补充并核验公告正文")
    if qualification_items:
        actions.append("将资格条款与本企业资质、业绩、人员和联合体条件逐项比对")
    else:
        actions.append("定位原文资格章节后再进行资质符合性判断")
    if deadline_state in {"紧急", "一周内", "两周内"}:
        actions.append("核对公告的投标截止、文件获取及澄清时间，形成倒排计划")
    elif deadline_state == "已过期":
        actions.append("确认是否存在延期、更正或重新招标公告")
    if fields.get("purchaser") not in _MISSING_VALUES:
        actions.append("结合招标人/采购人和项目内容确认客户覆盖与项目匹配度")

    known_evidence = [
        key for key in ("purchaser", "qualification", "deadline", "project_code") if evidence.get(key)
    ]
    evidence_ids = [
        str(evidence[key].get("evidence_id"))
        for key in known_evidence
        if isinstance(evidence.get(key), dict) and evidence[key].get("evidence_id")
    ]
    qualification_matrix = _qualification_matrix(qualification_items, company_profile)
    risks, missing_materials = _risk_materials(fields)
    if detail_status != "full":
        decision = "信息不足"
    elif announcement_type in {"中标公告", "成交公告", "废标公告", "终止公告"}:
        decision = "不建议参与"
    elif deadline_state == "已过期":
        decision = "不建议参与"
    elif not qualification_items or fields.get("bid_deadline") in _MISSING_VALUES:
        decision = "信息不足"
    elif company_profile:
        unresolved = any(row["status"] == "待核验" for row in qualification_matrix)
        decision = "有条件参与" if unresolved else "建议参与"
    else:
        decision = "有条件参与"

    schedule: list[dict[str, Any]] = []
    for label, field_name in (
        ("完成招标文件获取", "document_acquisition_end"),
        ("完成投标文件递交", "bid_deadline"),
        ("准备开标", "opening_time"),
    ):
        value = fields.get(field_name)
        if value in _MISSING_VALUES:
            continue
        schedule.append(
            {
                "milestone": label,
                "time": value,
                "evidence_id": (evidence.get(field_name) or {}).get("evidence_id"),
            }
        )
    return {
        "announcement_id": item.get("announcement_id"),
        "title": item.get("title"),
        "priority": priority,
        "priority_reasons": reasons,
        "deadline_urgency": deadline_state,
        "deadline_note": deadline_note,
        "qualification_readiness": "已提取待比对" if qualification_items else "待补充",
        "qualification_items": qualification_items,
        "gaps": gaps,
        "recommended_actions": actions[:4],
        "evidence_fields": known_evidence,
        "evidence_ids": evidence_ids,
        "evidence": evidence,
        "decision": decision,
        "decision_evidence_ids": evidence_ids,
        "qualification_matrix": qualification_matrix,
        "timeline": schedule,
        "technical_business_risks": risks,
        "missing_materials": missing_materials,
    }


def _portfolio_summary(projects: Sequence[dict[str, Any]]) -> str:
    if not projects:
        return "本轮没有形成可报告的公告条目。"
    counts: dict[str, int] = {}
    for project in projects:
        priority = str(project["priority"])
        counts[priority] = counts.get(priority, 0) + 1
    parts = [f"共 {len(projects)} 个项目"]
    for label in ("高", "中", "待核验", "低"):
        if counts.get(label):
            parts.append(f"{label}优先级 {counts[label]} 个")
    detail_missing = sum(1 for p in projects if "尚未获得可验证" in "；".join(p["gaps"]))
    if detail_missing:
        parts.append(f"其中 {detail_missing} 个仍需补齐官方详情")
    return "，".join(parts) + "。"


def _validated_llm_notes(
    data: dict[str, Any] | None, projects: list[dict[str, Any]]
) -> tuple[str | None, dict[str, str]]:
    if not isinstance(data, dict):
        return None, {}
    known = {str(project.get("announcement_id")): project for project in projects}
    notes: dict[str, str] = {}
    raw_notes = data.get("project_notes")
    if isinstance(raw_notes, list):
        for raw in raw_notes:
            if not isinstance(raw, dict):
                continue
            announcement_id = str(raw.get("announcement_id") or "")
            analysis = str(raw.get("analysis") or "").strip()
            evidence_fields = raw.get("evidence_fields")
            evidence_ids = raw.get("evidence_ids")
            project = known.get(announcement_id)
            if not project or not analysis or len(analysis) > 180:
                continue
            allowed_fields = set(project.get("evidence_fields") or [])
            allowed_ids = set(project.get("evidence_ids") or [])
            supplied_fields = set(map(str, evidence_fields or []))
            supplied_ids = set(map(str, evidence_ids or []))
            if supplied_ids:
                if not allowed_ids or not supplied_ids.issubset(allowed_ids):
                    continue
            elif not supplied_fields or not supplied_fields.issubset(allowed_fields):
                continue
            # Avoid trusting a model that starts inventing commercial facts.
            if re.search(r"(?:预算|金额|万元|亿元|联系人|必然中标|中标概率)", analysis):
                continue
            notes[announcement_id] = analysis
    summary = str(data.get("portfolio_summary") or "").strip()
    if len(summary) > 180 or re.search(r"(?:预算|金额|万元|亿元|联系人|中标概率)", summary):
        summary = ""
    return summary or None, notes


async def build_execution_analysis(
    items: list[dict[str, Any]],
    *,
    keywords: Sequence[str],
    regions: Sequence[str],
    start_date: str | None,
    end_date: str | None,
    company_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic insights, then optionally enrich with validated LLM notes."""
    prepared = [
        enrich_report_item(
            item,
            keywords=keywords,
            regions=regions,
            start_date=start_date,
            end_date=end_date,
        )
        for item in items
    ]
    today = datetime.now(TZ).date()
    projects = [
        _project_rule(item, today=today, company_profile=company_profile)
        for item in prepared
    ]
    result: dict[str, Any] = {
        "version": 1,
        "status": "rule_only",
        "provider": "rules",
        "portfolio_summary": _portfolio_summary(projects),
        "projects": projects,
        "generated_at": datetime.now(TZ).isoformat(),
        "company_profile_configured": bool(company_profile),
    }
    llm_projects = [
        {
            "announcement_id": project["announcement_id"],
            "title": project["title"],
            "priority": project["priority"],
            "deadline_urgency": project["deadline_urgency"],
            "gaps": project["gaps"],
            "recommended_actions": project["recommended_actions"],
            "evidence_fields": project["evidence_fields"],
            "evidence_ids": project["evidence_ids"],
            "decision": project["decision"],
        }
        for project in projects
        if project.get("evidence_fields")
    ]
    if not llm_projects:
        return result
    from app.core.config import get_settings

    if get_settings().app_env == "test":
        return result
    llm = await call_json_llm_chain(
        [
            {"role": "system", "content": REPORT_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": build_report_analysis_prompt(llm_projects)},
        ]
    )
    if not llm.success:
        return result
    summary, notes = _validated_llm_notes(llm.data, projects)
    # Portfolio prose is accepted only alongside at least one evidence-backed
    # project note; otherwise keep the deterministic summary.
    if not notes:
        return result
    for project in projects:
        note = notes.get(str(project.get("announcement_id")))
        if note:
            project["llm_note"] = note
    if summary:
        result["portfolio_summary"] = summary
    result["status"] = "rule_plus_llm"
    result["provider"] = llm.provider or "rules"
    result["model"] = llm.model
    return result


def analysis_preview(analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Small API/UI-safe summary; full evidence remains in execution history/report."""
    analysis = analysis or {}
    projects = analysis.get("projects") if isinstance(analysis.get("projects"), list) else []
    priority_counts: dict[str, int] = {}
    for project in projects:
        if isinstance(project, dict):
            priority = str(project.get("priority") or "待核验")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
    return {
        "status": analysis.get("status") or "rule_only",
        "provider": analysis.get("provider") or "rules",
        "portfolio_summary": analysis.get("portfolio_summary") or "",
        "priority_counts": priority_counts,
        "top_projects": [
            {
                "announcement_id": project.get("announcement_id"),
                "title": project.get("title"),
                "priority": project.get("priority"),
                "deadline_urgency": project.get("deadline_urgency"),
            }
            for project in projects[:3]
            if isinstance(project, dict)
        ],
    }
