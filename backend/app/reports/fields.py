"""报告字段抽取、展示格式与匹配依据（仅基于原文，不编造）."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Sequence

# 数据源正式中文名
SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "ccgp": "中国政府采购网",
    "cebpub": "中国招标投标公共服务平台",
    "login_portal": "登录态招采门户",
    "public_placeholder": "公开源占位",
    "fixture": "演示数据源",
}

_MISSING = "原文未明确说明"
_NOT_EXTRACTED = "本次未成功提取"

# 字段标签 → 正则
_FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("purchaser", re.compile(
        r"(?:采购人|招标人|采购单位|建设单位|甲方|招标单位)"
        r"[：:\s]*([^\n。；;]{2,80})"
    )),
    ("agency", re.compile(
        r"(?:代理机构|招标代理|采购代理|委托代理机构|交易平台)"
        r"[：:\s]*([^\n。；;]{2,80})"
    )),
    ("project_code", re.compile(
        r"(?:项目编号|招标编号|采购编号|公告编号|标段编号)"
        r"[：:\s]*([A-Za-z0-9\-_／/（）()]{4,64})"
    )),
    ("budget", re.compile(
        r"(?:预算金额|采购预算|预算金额为|预算为|最高限价|控制价|项目预算)"
        r"[：:\s]*([0-9]+(?:\.[0-9]+)?\s*(?:万元|亿元|元|万)?|"
        r"[^\n。；;]{2,40})"
    )),
    ("deadline", re.compile(
        r"(?:投标截止|递交截止|报名截止|截止时间|响应截止|开标时间|"
        r"公告结束时间|报价截止|文件递交截止)"
        r"[：:\s]*([^\n。；;]{4,40})"
    )),
    ("region_line", re.compile(
        r"(?:项目所在地|交货地点|建设地点|采购地点|项目地点|行政区划|"
        r"区域|所属地区|项目区域)"
        r"[：:\s]*([^\n。；;]{2,40})"
    )),
    ("content", re.compile(
        r"(?:采购内容|项目内容|招标内容|采购项目名称|项目概况)"
        r"[：:\s]*([^\n]{4,200})"
    )),
    ("announcement_type", re.compile(
        r"(公开招标|邀请招标|竞争性磋商|竞争性谈判|询价|单一来源|中标公告|成交公告|"
        r"更正公告|废标公告|资格预审|入围采购)"
    )),
    ("qualification", re.compile(
        r"(?:投标人资格|供应商资格|资格要求|申请人资格条件|资格条件)"
        r"[：:\s]*([^\n]{4,300})"
    )),
]


def source_display_name(source_name: str | None) -> str:
    if not source_name:
        return _MISSING
    return SOURCE_DISPLAY_NAMES.get(source_name, source_name)


def format_cn_date(value: Any) -> str:
    """统一为「2026年7月16日」；无法解析则返回缺失文案."""
    if value is None or value == "":
        return _MISSING
    if isinstance(value, datetime):
        return f"{value.year}年{value.month}月{value.day}日"
    if isinstance(value, date):
        return f"{value.year}年{value.month}月{value.day}日"
    s = str(value).strip()
    if not s:
        return _MISSING
    # ISO / 常见格式
    m = re.match(
        r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})",
        s,
    )
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y}年{mo}月{d}日"
    # 已是中文日期
    if re.match(r"\d{4}年\d{1,2}月\d{1,2}日", s):
        return s
    return s  # 保留原文片段，不强行编造


def format_cn_datetime(value: Any) -> str:
    if value is None or value == "":
        return _MISSING
    if isinstance(value, datetime):
        return (
            f"{value.year}年{value.month}月{value.day}日 "
            f"{value.hour:02d}:{value.minute:02d}"
        )
    s = str(value).strip()
    m = re.match(
        r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})",
        s,
    )
    if m:
        return (
            f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日 "
            f"{m.group(4)}:{m.group(5)}"
        )
    d = format_cn_date(s)
    return d


def _clean_capture(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.strip("：:；;，,。 ")
    return t[:200] if t else ""


def extract_fields(
    *,
    title: str = "",
    clean_content: str = "",
    summary: str = "",
    region: str | None = None,
    project_code: str | None = None,
    publish_time: Any = None,
) -> dict[str, str]:
    """从标题/正文抽取结构化字段；缺失统一占位，不编造."""
    blob = "\n".join(
        x for x in [title or "", summary or "", clean_content or ""] if x
    )
    out: dict[str, str] = {
        "purchaser": _MISSING,
        "agency": _MISSING,
        "project_code": (project_code or "").strip() or _MISSING,
        "budget": _MISSING,
        "deadline": _MISSING,
        "region": (region or "").strip() or _MISSING,
        "content": _MISSING,
        "announcement_type": _MISSING,
        "qualification": _MISSING,
        "project_name": _MISSING,
        "short_title": _short_title(title),
    }

    # 项目名称（正文优先；若与标题高度重合则留给报告层去重）
    m = re.search(r"(?:项目名称|采购项目)[：:\s]*([^\n]{4,120})", blob)
    if m:
        pn = _clean_capture(m.group(1))
        # 避免只抽到「招标公告」等后缀
        if pn in ("招标公告", "采购公告", "中标公告", "公告"):
            pn = title.strip() if title else pn
        out["project_name"] = pn
    elif title:
        out["project_name"] = title.strip()

    for key, pat in _FIELD_PATTERNS:
        if key == "project_code" and out["project_code"] != _MISSING:
            continue
        if key == "region_line" and out["region"] != _MISSING:
            continue
        m = pat.search(blob)
        if not m:
            continue
        if key == "announcement_type":
            out["announcement_type"] = m.group(1)
        elif key == "region_line":
            out["region"] = _clean_capture(m.group(1))
        elif key == "qualification":
            out["qualification"] = _clean_capture(m.group(1))
        elif key == "content":
            out["content"] = _clean_capture(m.group(1))
        elif key == "deadline":
            raw_dl = _clean_capture(m.group(1))
            cn = format_cn_date(raw_dl)
            out["deadline"] = cn if cn != _MISSING else raw_dl
        elif key in out:
            out[key] = _clean_capture(m.group(1))

    # 采购人：标题中常见「某公司/局…采购/招标」（优先匹配更长组织后缀）
    if out["purchaser"] == _MISSING and title:
        m = re.match(
            r"^(.+?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))"
            r".{0,60}(?:采购|招标|询价|磋商|入围)",
            title,
        )
        if not m:
            m = re.match(
                r"^(.+?(?:集团公司|集团|大学|医院|中心|银行股份有限公司|银行|"
                r"管理局|局|厅|委员会|部))"
                r".{0,60}(?:采购|招标|询价|磋商|入围)",
                title,
            )
        if m:
            out["purchaser"] = _clean_capture(m.group(1))

    # 采购内容兜底：正文非元数据行，或从标题去掉后缀
    if out["content"] == _MISSING:
        if clean_content:
            # 接口型详情往往只有元数据行：用「项目名称」去后缀作采购内容
            m_name = re.search(r"项目名称[：:\s]*([^\n]{4,120})", clean_content)
            if m_name:
                stripped = re.sub(
                    r"(公开招标公告|招标公告|中标公告|采购公告|成交公告)$",
                    "",
                    m_name.group(1).strip(),
                ).strip()
                if stripped:
                    out["content"] = stripped[:180]
        if out["content"] == _MISSING and title:
            stripped = re.sub(
                r"(公开招标公告|招标公告|中标公告|采购公告|成交公告)$",
                "",
                title,
            ).strip()
            if stripped:
                out["content"] = stripped[:180]

    # 发布时间：优先结构化字段，其次正文「公告发布时间」
    if publish_time:
        out["publish_time_cn"] = format_cn_date(publish_time)
    else:
        m = re.search(r"公告发布时间[：:\s]*([^\n]{8,20})", blob)
        out["publish_time_cn"] = (
            format_cn_date(m.group(1).strip()) if m else _MISSING
        )
    return out


def _short_title(title: str, max_len: int = 28) -> str:
    t = re.sub(r"\s+", "", (title or "").strip())
    if not t:
        return _MISSING
    # 去掉常见后缀噪声
    t = re.sub(r"(公开招标公告|招标公告|中标公告|成交公告|更正公告)$", "", t)
    if len(t) <= max_len:
        return t or (title[:max_len] if title else _MISSING)
    return t[: max_len - 1] + "…"


def build_match_basis(
    *,
    title: str,
    clean_content: str = "",
    region_field: str | None = None,
    keywords: Sequence[str],
    regions: Sequence[str],
    start_date: str | None,
    end_date: str | None,
    publish_time: Any = None,
) -> dict[str, str]:
    """关键词 / 区域 / 时间匹配依据（可审计）."""
    blob = " ".join(filter(None, [title, region_field or "", clean_content[:3000]]))

    # 关键词
    hit_kw = [k for k in keywords if k and k in blob]
    if not keywords:
        kw_reason = "查询未指定关键词，未做关键词过滤"
    elif hit_kw:
        places = []
        for k in hit_kw:
            loc = []
            if k in (title or ""):
                loc.append("标题")
            if region_field and k in region_field:
                loc.append("地区字段")
            if k in (clean_content or ""):
                loc.append("正文")
            places.append(f"「{k}」出现于{'/'.join(loc) or '文本'}")
        kw_reason = "；".join(places)
    else:
        kw_reason = "正文/标题未直接命中查询关键词（可能由列表摘要阶段入选）"

    # 区域
    if not regions:
        region_reason = "查询未指定区域"
    else:
        hits: list[str] = []
        foreign: list[str] = []
        for r in regions:
            short = (
                r.replace("省", "")
                .replace("市", "")
                .replace("自治区", "")
                .replace("特别行政区", "")
            )
            if r in blob or (short and short in blob):
                where = []
                if r in (title or "") or (short and short in (title or "")):
                    where.append("标题")
                if region_field and (r in region_field or (short and short in region_field)):
                    where.append("地区字段")
                if r in (clean_content or "") or (short and short in (clean_content or "")):
                    where.append("正文")
                hits.append(f"「{r}」见于{'/'.join(where) or '文本'}")
        # 检测标题中常见外省词（提示质量）
        other_provinces = (
            "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
            "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
            "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
            "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
        )
        query_tokens = set()
        for r in regions:
            query_tokens.add(r)
            query_tokens.add(
                r.replace("省", "").replace("市", "").replace("自治区", "")
            )
        for p in other_provinces:
            if p in (title or "") and p not in query_tokens and not any(
                p in qt for qt in query_tokens
            ):
                foreign.append(p)
        if hits:
            region_reason = "；".join(hits)
            if foreign:
                region_reason += (
                    f"。注意：标题另含地区词「{'、'.join(foreign[:3])}」，"
                    "请结合正文核对是否属查询区域"
                )
        else:
            region_reason = (
                "标题/正文/地区字段未检出查询区域词；"
                "若仍入选，可能因列表阶段区域字段缺失，请人工复核"
            )
            if foreign:
                region_reason += f"。标题出现其他地区：{'、'.join(foreign[:3])}"

    # 时间
    pub_cn = format_cn_date(publish_time)
    if not start_date and not end_date:
        time_reason = "查询未指定统计周期"
    elif publish_time is None or pub_cn == _MISSING:
        time_reason = "原文未提供可解析的发布时间，时间条件无法严格核验"
    else:
        parts = [f"发布日期 {pub_cn}"]
        if start_date:
            parts.append(f"不早于 {format_cn_date(start_date)}")
        if end_date:
            parts.append(f"不晚于 {format_cn_date(end_date)}")
        time_reason = "；".join(parts)

    return {
        "keyword": kw_reason,
        "region": region_reason,
        "time": time_reason,
        "combined": f"关键词：{kw_reason}；区域：{region_reason}；时间：{time_reason}",
    }


def data_completeness(fields: dict[str, str], *, has_attachments: bool) -> dict[str, Any]:
    """数据完整度：核心字段有值比例."""
    keys = [
        "purchaser",
        "region",
        "project_code",
        "budget",
        "deadline",
        "content",
        "publish_time_cn",
    ]
    filled = 0
    for k in keys:
        v = fields.get(k) or ""
        if v and v not in (_MISSING, _NOT_EXTRACTED, "—", "-"):
            filled += 1
    if has_attachments:
        filled += 0.5
        total = len(keys) + 0.5
    else:
        total = float(len(keys))
    ratio = filled / total if total else 0
    pct = int(round(ratio * 100))
    if pct >= 80:
        level = "较完整"
    elif pct >= 50:
        level = "部分完整"
    else:
        level = "信息偏少"
    return {"percent": pct, "level": level, "label": f"{pct}%（{level}）"}


def attachment_status(
    *,
    links: list[str] | None,
    detail_fetched: bool = True,
    extract_failed: bool = False,
    requires_login: bool = False,
    login_only_hint: bool = False,
) -> dict[str, Any]:
    """附件状态语义，避免一律显示「无」."""
    links = [x for x in (links or []) if x]
    if extract_failed:
        return {
            "status": "extract_failed",
            "label": "提取失败",
            "links": [],
            "note": "详情页附件链接解析失败，非确认无附件",
        }
    if not detail_fetched:
        return {
            "status": "detail_missing",
            "label": "详情未获取",
            "links": [],
            "note": "未成功打开详情页，附件情况未知",
        }
    if links:
        return {
            "status": "found",
            "label": f"发现 {len(links)} 个公开附件",
            "links": links,
            "note": "",
        }
    if requires_login or login_only_hint:
        return {
            "status": "login_required",
            "label": "登录后可见",
            "links": [],
            "note": "公开页未提供附件链接，完整附件可能需登录后查看",
        }
    return {
        "status": "none_public",
        "label": "未发现公开附件",
        "links": [],
        "note": "详情页已获取，但未解析到可公开访问的附件链接",
    }


def enrich_report_item(
    item: dict[str, Any],
    *,
    keywords: Sequence[str],
    regions: Sequence[str],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    """为报告条目补齐展示字段（不修改库表）."""
    title = item.get("title") or ""
    clean = item.get("clean_content") or ""
    summary = item.get("summary") or ""
    region = item.get("region")
    fields = extract_fields(
        title=title,
        clean_content=clean,
        summary=summary,
        region=region,
        project_code=item.get("project_code"),
        publish_time=item.get("publish_time"),
    )
    # 避免「标题」与「项目名称」完全重复展示
    if fields.get("project_name") == title or fields.get("project_name") == fields.get(
        "short_title"
    ):
        # 详情里用 short_title 作简称，project_name 若与 title 相同则标记跳过重复
        fields["project_name_display"] = ""
    else:
        fields["project_name_display"] = fields.get("project_name") or ""

    match = build_match_basis(
        title=title,
        clean_content=clean,
        region_field=region or fields.get("region"),
        keywords=keywords,
        regions=regions,
        start_date=start_date,
        end_date=end_date,
        publish_time=item.get("publish_time"),
    )
    att = attachment_status(
        links=item.get("attachment_links") or [],
        detail_fetched=item.get("detail_fetched", True),
        extract_failed=bool(item.get("attachment_extract_failed")),
        requires_login=bool(item.get("requires_login")),
        login_only_hint=bool(item.get("attachment_login_only")),
    )
    complete = data_completeness(fields, has_attachments=bool(att["links"]))

    out = dict(item)
    out["fields"] = fields
    out["match_basis"] = match
    out["attachment"] = att
    out["completeness"] = complete
    out["source_display"] = source_display_name(item.get("source_name"))
    out["publish_time_cn"] = fields["publish_time_cn"]
    out["short_title"] = fields["short_title"]
    return out


def verify_funnel_closed(stats: dict[str, int]) -> tuple[bool, list[str]]:
    """校验数据处理漏斗数字是否闭合，返回 (ok, 说明列表)."""
    notes: list[str] = []
    raw = stats.get("raw_result_count", 0)
    list_f = stats.get("list_filtered_out", 0)
    cap = stats.get("detail_cap_skipped", 0)
    d_fail = stats.get("detail_failed", 0)
    d_filt = stats.get("detail_filtered_out", 0)
    cand = stats.get("candidates_count", 0)
    left = raw - list_f - cap - d_fail - d_filt - cand
    if left != 0:
        notes.append(
            f"列表漏斗未闭合：原始{raw} − 列表过滤{list_f} − 上限跳过{cap} "
            f"− 详情失败{d_fail} − 详情过滤{d_filt} − 候选{cand} = {left}（应为0）"
        )
    merged = stats.get("cross_source_merge_count", 0)
    prim = stats.get("primary_count", 0)
    if cand != merged + prim and cand > 0:
        notes.append(
            f"去重漏斗：候选{cand} ≠ 跨源合并{merged} + 主记录{prim}"
        )
    new_c = stats.get("incremental_count", 0)
    upd = stats.get("update_count", 0)
    skip = stats.get("skipped_already_delivered", 0)
    report_n = stats.get("report_item_count", 0)
    if report_n != new_c + upd:
        notes.append(f"报告条目{report_n} ≠ 新增{new_c} + 更新{upd}")
    # 主记录与增量：primaries 应覆盖 new+update+skip（允许库内合并导致 prim 含已存在）
    if prim > 0 and new_c + upd + skip > prim + stats.get("db_merge_count", 0):
        notes.append(
            f"增量合计({new_c}+{upd}+{skip}) 大于主记录{prim}，请核对"
        )
    ok = len(notes) == 0
    if ok:
        notes.append(
            f"闭合校验通过：{raw} = {list_f}+{cap}+{d_fail}+{d_filt}+{cand}；"
            f"候选{cand} → 合并{merged} + 主记录{prim}；"
            f"报告{report_n} = 新增{new_c}+更新{upd}"
        )
    return ok, notes
