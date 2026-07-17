"""Word 招投标报告生成（结构化、可审计、统计闭合）."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.reports.fields import (
    _MISSING,
    enrich_report_item,
    format_cn_date,
    format_cn_datetime,
    source_display_name,
    verify_funnel_closed,
)
from app.reports.filenames import build_report_filename

TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class SourceRunStat:
    """单数据源执行情况."""

    source_name: str
    display_name: str = ""
    status: str = "unknown"  # success | failed | skipped
    message: str = ""
    raw_count: int = 0
    list_kept: int = 0
    detail_success: int = 0
    final_contributed: int = 0  # 进入报告的条目数（按主来源）


@dataclass
class ReportContext:
    system_name: str = "FusionBid 智标聚合助手"
    original_query: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(TZ))
    execute_type: str = "立即执行"
    data_mode: str = "实时数据"
    execution_status: str = "success"  # success | partial | failed
    keywords: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    schedule_desc: str = "无（立即执行）"
    # 兼容旧字段：成功源标识列表
    sources: list[str] = field(default_factory=list)
    sources_requested: list[str] = field(default_factory=list)
    sources_succeeded: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)
    source_stats: list[SourceRunStat] = field(default_factory=list)
    # 闭合漏斗
    raw_result_count: int = 0
    list_filtered_out: int = 0
    detail_cap_skipped: int = 0
    detail_failed: int = 0
    detail_success_count: int = 0
    detail_filtered_out: int = 0
    candidates_count: int = 0
    cross_source_merge_count: int = 0
    primary_count: int = 0
    db_merge_count: int = 0
    filtered_out_count: int = 0  # 兼容：list+detail 过滤合计
    duplicate_count: int = 0
    final_count: int = 0  # 去重后主记录
    incremental_count: int = 0
    update_count: int = 0
    skipped_already_delivered: int = 0
    # 结果
    items: list[dict[str, Any]] = field(default_factory=list)
    crawl_time: str | None = None
    extra_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def funnel_dict(self) -> dict[str, int]:
        return {
            "raw_result_count": self.raw_result_count,
            "list_filtered_out": self.list_filtered_out,
            "detail_cap_skipped": self.detail_cap_skipped,
            "detail_failed": self.detail_failed,
            "detail_filtered_out": self.detail_filtered_out,
            "candidates_count": self.candidates_count,
            "cross_source_merge_count": self.cross_source_merge_count,
            "primary_count": self.primary_count or self.final_count,
            "incremental_count": self.incremental_count,
            "update_count": self.update_count,
            "skipped_already_delivered": self.skipped_already_delivered,
            "report_item_count": len(self.items),
            "db_merge_count": self.db_merge_count,
        }


def _set_run_font(
    run,
    *,
    size: int = 11,
    bold: bool = False,
    name: str = "宋体",
    color: RGBColor | None = None,
) -> None:
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = name
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), name)
    rFonts.set(qn("w:ascii"), name if name != "宋体" else "Times New Roman")
    if color is not None:
        run.font.color.rgb = color


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        _set_run_font(run, size=16 if level == 1 else 13, bold=True, name="黑体")


def _add_para(
    doc: Document,
    text: str,
    *,
    bold: bool = False,
    size: int = 11,
    color: RGBColor | None = None,
    center: bool = False,
) -> Any:
    p = doc.add_paragraph()
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    _set_run_font(run, size=size, bold=bold, color=color)
    return p


def _add_label_value(doc: Document, label: str, value: str, *, size: int = 11) -> None:
    p = doc.add_paragraph()
    r1 = p.add_run(f"{label}")
    _set_run_font(r1, size=size, bold=True)
    r2 = p.add_run(str(value) if value is not None else _MISSING)
    _set_run_font(r2, size=size)


def _add_hyperlink(paragraph, url: str, text: str = "查看原始公告") -> None:
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "22")
    rPr.append(sz)
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:eastAsia"), "宋体")
    rPr.append(rFonts)
    new_run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def _set_cell_shading(cell, fill: str) -> None:
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def _set_cell_text(cell, text: str, *, bold: bool = False, size: int = 9) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    run = p.add_run(str(text) if text is not None else "")
    _set_run_font(run, size=size, bold=bold)


def _add_table(doc: Document, headers: list[str], rows: list[list[str]], *, col_widths_cm: list[float] | None = None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        _set_cell_text(cell, h, bold=True, size=9)
        try:
            _set_cell_shading(cell, "D9E8F5")
        except Exception:  # noqa: BLE001
            pass
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            _set_cell_text(table.rows[ri + 1].cells[ci], val, size=9)
    if col_widths_cm:
        for row in table.rows:
            for i, w in enumerate(col_widths_cm):
                if i < len(row.cells):
                    row.cells[i].width = Cm(w)
    return table


def _status_label(status: str) -> str:
    return {
        "success": "成功（全部请求数据源已完成）",
        "partial": "部分成功（存在失败或跳过的数据源）",
        "failed": "失败",
        "running": "执行中",
    }.get(status, status or "未知")


def _period_text(ctx: ReportContext) -> str:
    if ctx.start_date or ctx.end_date:
        a = format_cn_date(ctx.start_date) if ctx.start_date else "不限"
        b = format_cn_date(ctx.end_date) if ctx.end_date else "不限"
        return f"{a} 至 {b}"
    return "未指定统计周期"


def _prepare_items(ctx: ReportContext) -> list[dict[str, Any]]:
    return [
        enrich_report_item(
            it,
            keywords=ctx.keywords,
            regions=ctx.regions,
            start_date=ctx.start_date,
            end_date=ctx.end_date,
        )
        for it in ctx.items
    ]


def _add_header_footer(doc: Document, ctx: ReportContext) -> None:
    section = doc.sections[0]
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hr = hp.add_run(f"{ctx.system_name} · 招投标信息聚合报告")
    _set_run_font(hr, size=9, name="宋体", color=RGBColor(0x66, 0x66, 0x66))

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _add_page_field(paragraph, instr: str) -> None:
        run = paragraph.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr_text = OxmlElement("w:instrText")
        instr_text.set(qn("xml:space"), "preserve")
        instr_text.text = instr
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        run._r.append(fld_begin)
        run._r.append(instr_text)
        run._r.append(fld_sep)
        run._r.append(fld_end)

    r0 = fp.add_run("第 ")
    _set_run_font(r0, size=9, color=RGBColor(0x66, 0x66, 0x66))
    _add_page_field(fp, " PAGE ")
    r1 = fp.add_run(" 页 / 共 ")
    _set_run_font(r1, size=9, color=RGBColor(0x66, 0x66, 0x66))
    _add_page_field(fp, " NUMPAGES ")
    r2 = fp.add_run(" 页")
    _set_run_font(r2, size=9, color=RGBColor(0x66, 0x66, 0x66))


def _page1_cover(doc: Document, ctx: ReportContext, items: list[dict]) -> None:
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(ctx.system_name)
    _set_run_font(r, size=22, bold=True, name="黑体")

    s = doc.add_paragraph()
    s.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = s.add_run("招投标信息聚合报告")
    _set_run_font(r2, size=18, bold=True, name="黑体")

    doc.add_paragraph()
    _add_label_value(doc, "用户原始问题：", ctx.original_query or _MISSING, size=12)
    _add_label_value(
        doc,
        "标准化查询条件：",
        (
            f"关键词：{'、'.join(ctx.keywords) if ctx.keywords else '（未指定）'}；"
            f"区域：{'、'.join(ctx.regions) if ctx.regions else '（未指定）'}；"
            f"周期：{_period_text(ctx)}；"
            f"执行方式：{ctx.execute_type}；"
            f"频率：{ctx.schedule_desc}"
        ),
    )
    _add_label_value(doc, "统计周期：", _period_text(ctx))
    _add_label_value(doc, "有效结果数量（本报告条目）：", str(len(items)))
    succ = len(ctx.sources_succeeded or ctx.sources)
    req = len(ctx.sources_requested) or succ
    _add_label_value(
        doc,
        "成功数据源数量：",
        f"{succ} / {req}" if req else str(succ),
    )
    _add_label_value(doc, "本次新增数量：", str(ctx.incremental_count))
    _add_label_value(doc, "本次内容更新数量：", str(ctx.update_count))
    _add_label_value(doc, "数据模式：", ctx.data_mode)
    _add_label_value(
        doc,
        "报告生成时间：",
        format_cn_datetime(ctx.generated_at),
    )

    st = ctx.execution_status or "success"
    p = doc.add_paragraph()
    r1 = p.add_run("执行状态：")
    _set_run_font(r1, size=12, bold=True)
    color = {
        "success": RGBColor(0x1E, 0x84, 0x4E),
        "partial": RGBColor(0xB7, 0x6E, 0x00),
        "failed": RGBColor(0xC0, 0x39, 0x2B),
    }.get(st, RGBColor(0x33, 0x33, 0x33))
    r2 = p.add_run(_status_label(st))
    _set_run_font(r2, size=12, bold=True, color=color)

    if st == "partial":
        box = doc.add_paragraph()
        run = box.add_run(
            "【注意】本次为部分成功：至少有一个数据源失败或被跳过。"
            "报告中的有效结果仅来自已成功数据源，请结合第二页「数据源执行情况」阅读，"
            "勿将结果理解为全网完整覆盖。"
        )
        _set_run_font(run, size=11, bold=True, color=RGBColor(0xB7, 0x6E, 0x00))

    if ctx.warnings:
        _add_heading(doc, "警告与提示", 2)
        for w in ctx.warnings:
            _add_para(doc, f"· {w}", color=RGBColor(0xB7, 0x6E, 0x00))

    if ctx.data_mode != "实时数据":
        _add_para(
            doc,
            "【注意】本报告包含演示/测试数据，请勿当作全部 live 结果。",
            bold=True,
            color=RGBColor(0xC0, 0x39, 0x2B),
        )


def _page2_summary(doc: Document, ctx: ReportContext, items: list[dict]) -> None:
    doc.add_page_break()
    _add_heading(doc, "一、执行摘要", 1)
    src_ok = "、".join(source_display_name(s) for s in (ctx.sources_succeeded or ctx.sources)) or "无"
    src_fail = "、".join(
        source_display_name(s) for s in (ctx.sources_failed or {})
    ) or "无"
    _add_para(
        doc,
        (
            f"围绕「{ctx.original_query or '（空查询）'}」，"
            f"在统计周期 {_period_text(ctx)} 内，"
            f"共请求 {len(ctx.sources_requested) or '—'} 个数据源，"
            f"成功 {len(ctx.sources_succeeded or ctx.sources)} 个"
            f"（{src_ok}），"
            f"失败/跳过 {len(ctx.sources_failed or {})} 个（{src_fail}）。"
            f"原始列表 {ctx.raw_result_count} 条，"
            f"经过滤与去重后主记录 {ctx.primary_count or ctx.final_count} 条，"
            f"本报告收录有效结果 {len(items)} 条"
            f"（新增 {ctx.incremental_count}、更新 {ctx.update_count}）。"
        ),
    )

    _add_heading(doc, "二、数据源执行情况", 1)
    rows: list[list[str]] = []
    if ctx.source_stats:
        for ss in ctx.source_stats:
            rows.append(
                [
                    ss.display_name or source_display_name(ss.source_name),
                    {"success": "成功", "failed": "失败", "skipped": "跳过"}.get(
                        ss.status, ss.status
                    ),
                    str(ss.raw_count),
                    str(ss.detail_success),
                    str(ss.final_contributed),
                    (ss.message or "—")[:40],
                ]
            )
    else:
        # 回退：从 succeeded/failed 拼表
        all_names = list(
            dict.fromkeys(
                (ctx.sources_requested or [])
                + (ctx.sources_succeeded or ctx.sources or [])
                + list((ctx.sources_failed or {}).keys())
            )
        )
        for name in all_names:
            if name in (ctx.sources_failed or {}):
                st = "失败"
                msg = (ctx.sources_failed or {}).get(name, "")[:40]
            elif name in (ctx.sources_succeeded or ctx.sources or []):
                st = "成功"
                msg = "已完成检索"
            else:
                st = "未执行"
                msg = "—"
            contrib = sum(1 for it in items if it.get("source_name") == name)
            rows.append([source_display_name(name), st, "—", "—", str(contrib), msg])
    if not rows:
        rows.append(["—", "—", "0", "0", "0", "无数据源记录"])
    _add_table(
        doc,
        ["数据源", "状态", "列表条数", "详情成功", "报告贡献", "说明"],
        rows,
        col_widths_cm=[4.0, 1.5, 1.8, 1.8, 1.8, 4.5],
    )
    _add_para(
        doc,
        "说明：「报告贡献」指本报告条目中主来源为该数据源的数量；"
        "失败数据源贡献为 0，不代表其网站无相关公告。",
        size=9,
        color=RGBColor(0x66, 0x66, 0x66),
    )

    _add_heading(doc, "三、数据处理漏斗", 1)
    fd = ctx.funnel_dict()
    ok, notes = verify_funnel_closed(fd)
    funnel_rows = [
        ["原始列表结果", str(ctx.raw_result_count), "各成功数据源列表页合计"],
        ["列表阶段过滤排除", str(ctx.list_filtered_out), "关键词/区域/时间/相关性"],
        ["详情上限未抓取", str(ctx.detail_cap_skipped), "超过每源详情抓取上限"],
        ["详情抓取失败", str(ctx.detail_failed), "打开详情页异常"],
        ["详情阶段过滤排除", str(ctx.detail_filtered_out), "详情二次条件不匹配"],
        ["进入去重候选", str(ctx.candidates_count), "待跨源去重"],
        ["跨源合并减少", str(ctx.cross_source_merge_count), "同项目多来源合并"],
        ["去重后主记录", str(ctx.primary_count or ctx.final_count), "本批唯一主记录"],
        ["库内已存在合并", str(ctx.db_merge_count), "与历史库记录合并"],
        ["历史已交付跳过", str(ctx.skipped_already_delivered), "增量：已推送且无变化"],
        ["本次新增", str(ctx.incremental_count), "首次交付"],
        ["本次内容更新", str(ctx.update_count), "已推送但内容变化"],
        ["本报告条目", str(len(items)), "新增 + 更新"],
    ]
    _add_table(
        doc,
        ["阶段", "数量", "说明"],
        funnel_rows,
        col_widths_cm=[4.5, 2.0, 9.0],
    )
    for n in notes:
        _add_para(
            doc,
            f"{'✓' if ok else '!'} {n}",
            size=9,
            color=RGBColor(0x1E, 0x84, 0x4E) if ok else RGBColor(0xC0, 0x39, 0x2B),
        )

    _add_heading(doc, "四、重点项目 Top 3", 1)
    if not items:
        _add_para(doc, "本次无有效结果条目。")
    else:
        # 按完整度 + 有预算优先
        ranked = sorted(
            items,
            key=lambda x: (
                x.get("completeness", {}).get("percent", 0),
                1 if (x.get("fields") or {}).get("budget") not in (_MISSING, None, "") else 0,
            ),
            reverse=True,
        )[:3]
        for i, it in enumerate(ranked, 1):
            f = it.get("fields") or {}
            _add_para(
                doc,
                f"{i}. {it.get('short_title') or it.get('title') or _MISSING}",
                bold=True,
            )
            _add_para(
                doc,
                (
                    f"地区：{f.get('region', _MISSING)}；"
                    f"采购人：{f.get('purchaser', _MISSING)}；"
                    f"预算：{f.get('budget', _MISSING)}；"
                    f"截止：{f.get('deadline', _MISSING)}；"
                    f"完整度：{it.get('completeness', {}).get('label', '—')}；"
                    f"来源：{it.get('source_display', '—')}"
                ),
                size=10,
            )

    _add_heading(doc, "五、数据质量提示", 1)
    tips = [
        "所有字段均来自公开页面抽取；无法确认时标注「原文未明确说明」或「本次未成功提取」，系统不编造。",
        "区域匹配依据见各项目「匹配依据」；标题含外地地名时请人工核对是否误召回。",
        "附件状态区分：未发现公开附件 / 提取失败 / 登录后可见 / 详情未获取，不再统一写「无」。",
        "部分成功时，失败数据源的公告不会出现在本报告中。",
    ]
    tips.extend(ctx.extra_notes)
    for t in tips:
        _add_para(doc, f"· {t}")


def _page3_overview(doc: Document, ctx: ReportContext, items: list[dict]) -> None:
    doc.add_page_break()
    _add_heading(doc, "六、项目总览", 1)
    if not items:
        _add_para(doc, "无项目可展示。")
        return
    rows = []
    for i, it in enumerate(items, 1):
        f = it.get("fields") or {}
        rows.append(
            [
                str(i),
                it.get("short_title") or _MISSING,
                f.get("region") or _MISSING,
                f.get("purchaser") or _MISSING,
                f.get("announcement_type") or _MISSING,
                it.get("publish_time_cn") or _MISSING,
                f.get("deadline") or _MISSING,
                f.get("budget") or _MISSING,
                it.get("source_display") or source_display_name(it.get("source_name")),
            ]
        )
    _add_table(
        doc,
        [
            "序号",
            "项目简称",
            "项目地区",
            "采购人",
            "公告类型",
            "发布时间",
            "截止时间",
            "预算金额",
            "来源",
        ],
        rows,
        col_widths_cm=[1.0, 2.8, 1.6, 2.2, 1.6, 1.8, 1.8, 1.6, 2.0],
    )


def _detail_pages(doc: Document, ctx: ReportContext, items: list[dict]) -> None:
    doc.add_page_break()
    _add_heading(doc, "七、项目结构化详情", 1)
    if not items:
        _add_para(doc, "无详情可展示。")
        return

    for i, it in enumerate(items, 1):
        f = it.get("fields") or {}
        title = it.get("title") or _MISSING
        short = it.get("short_title") or title
        label = it.get("change_label") or ""
        head = f"{i}. {short}"
        if label:
            head = f"{head} 【{label}】"
        _add_heading(doc, head, 2)

        # 完整标题仅显示一次
        if title and title != short:
            _add_label_value(doc, "完整标题：", title)
        # 项目名称若与标题/简称重复则跳过
        pn = f.get("project_name_display") or ""
        if pn and pn not in (title, short):
            _add_label_value(doc, "项目名称：", pn)

        status_bits = []
        if label:
            status_bits.append(label)
        if it.get("is_update"):
            status_bits.append("内容更新")
        elif it.get("is_new"):
            status_bits.append("新增")
        _add_label_value(
            doc,
            "公告状态：",
            "、".join(status_bits) if status_bits else "有效收录",
        )
        _add_label_value(doc, "项目编号：", f.get("project_code") or _MISSING)
        _add_label_value(doc, "采购人：", f.get("purchaser") or _MISSING)
        _add_label_value(doc, "代理机构：", f.get("agency") or _MISSING)
        _add_label_value(doc, "项目地区：", f.get("region") or _MISSING)
        _add_label_value(doc, "公告类型：", f.get("announcement_type") or _MISSING)
        _add_label_value(doc, "采购内容：", f.get("content") or _MISSING)
        _add_label_value(doc, "预算金额：", f.get("budget") or _MISSING)
        _add_label_value(doc, "发布时间：", it.get("publish_time_cn") or _MISSING)
        _add_label_value(doc, "截止时间：", f.get("deadline") or _MISSING)
        _add_label_value(doc, "资格要求：", f.get("qualification") or _MISSING)
        _add_label_value(
            doc,
            "数据完整度：",
            (it.get("completeness") or {}).get("label") or _MISSING,
        )

        att = it.get("attachment") or {}
        _add_label_value(doc, "附件状态：", att.get("label") or "未发现公开附件")
        if att.get("note"):
            _add_para(doc, f"附件说明：{att['note']}", size=10, color=RGBColor(0x66, 0x66, 0x66))
        for a in att.get("links") or []:
            pp = doc.add_paragraph()
            r = pp.add_run("附件：")
            _set_run_font(r, size=10)
            if str(a).startswith("http"):
                _add_hyperlink(pp, str(a), "下载/查看附件")
            else:
                rr = pp.add_run(str(a))
                _set_run_font(rr, size=10)

        mb = it.get("match_basis") or {}
        _add_para(doc, "匹配依据：", bold=True)
        _add_para(doc, f"· 关键词：{mb.get('keyword', _MISSING)}", size=10)
        _add_para(doc, f"· 区域：{mb.get('region', _MISSING)}", size=10)
        _add_para(doc, f"· 时间：{mb.get('time', _MISSING)}", size=10)

        # 来源链接 — 超链接文字
        p = doc.add_paragraph()
        r = p.add_run("来源：")
        _set_run_font(r, size=11, bold=True)
        r2 = p.add_run(f"{it.get('source_display') or source_display_name(it.get('source_name'))} · ")
        _set_run_font(r2, size=11)
        url = it.get("source_url") or ""
        if str(url).startswith("http"):
            _add_hyperlink(p, str(url), "查看原始公告")
        else:
            rr = p.add_run(_MISSING)
            _set_run_font(rr, size=11)

        related = it.get("related_urls") or []
        extra_urls = [u for u in related if u and u != url]
        if extra_urls:
            _add_para(doc, "相关来源：", bold=True, size=10)
            for u in extra_urls[:5]:
                pp = doc.add_paragraph()
                if str(u).startswith("http"):
                    _add_hyperlink(pp, str(u), "查看相关公告")
                else:
                    run = pp.add_run(str(u))
                    _set_run_font(run, size=10)

        if it.get("is_update"):
            _add_para(
                doc,
                "说明：该条为已推送公告的内容更新，非全新公告。",
                bold=True,
                size=10,
            )


def _last_page(doc: Document, ctx: ReportContext, items: list[dict]) -> None:
    doc.add_page_break()
    _add_heading(doc, "八、数据处理说明与声明", 1)

    _add_heading(doc, "数据处理说明", 2)
    for line in [
        "流程：多源列表检索 → 列表条件过滤 → 详情抓取 → 详情二次过滤 → 跨源去重 → 增量判定 → 生成报告。",
        "过滤维度：关键词、区域、发布时间、招标/采购相关性。",
        "去重：同批跨源合并 + 库内项目编号/内容指纹合并；主记录优先信息更完整的官方来源。",
        "增量：相对本任务历史交付记录，仅将新增或内容变化条目写入本报告。",
        "字段抽取：基于正文规则匹配；缺失字段不推断、不补全。",
    ]:
        _add_para(doc, f"· {line}")

    _add_heading(doc, "失败数据源及原因", 2)
    if ctx.sources_failed:
        for name, reason in ctx.sources_failed.items():
            _add_para(
                doc,
                f"· {source_display_name(name)}（{name}）：{reason or '未知原因'}",
            )
    else:
        _add_para(doc, "· 无失败数据源记录。")

    # 成功但零贡献
    succ = set(ctx.sources_succeeded or ctx.sources or [])
    contrib = {it.get("source_name") for it in items}
    zero = [s for s in succ if s not in contrib]
    if zero:
        _add_heading(doc, "成功但未贡献报告条目的数据源", 2)
        for s in zero:
            _add_para(
                doc,
                f"· {source_display_name(s)}：检索成功，但经过滤/去重/增量后无条目进入本报告"
                "（可能列表无命中、详情失败或均被过滤）。",
            )

    _add_label_value(
        doc,
        "抓取时间：",
        format_cn_datetime(ctx.crawl_time)
        if ctx.crawl_time
        else format_cn_datetime(ctx.generated_at),
    )
    _add_label_value(doc, "报告生成时间：", format_cn_datetime(ctx.generated_at))
    _add_label_value(doc, "执行状态：", _status_label(ctx.execution_status))

    _add_heading(doc, "数据质量与免责声明", 2)
    for line in [
        "本报告信息来源于公开网页自动抓取与规则抽取，可能存在延迟、缺漏或解析偏差。",
        "预算、截止时间、资格要求等关键商务条件请以原始公告为准。",
        "系统不绕过登录验证、不破解付费内容；登录态源不可用时自动跳过且不影响公开源。",
        "请勿将本报告作为唯一投标决策依据。",
        f"执行状态：{_status_label(ctx.execution_status)}。"
        + (
            "部分数据源未成功时，结果覆盖范围有限。"
            if ctx.execution_status == "partial"
            else ""
        ),
    ]:
        _add_para(doc, f"· {line}")


def build_word_document(ctx: ReportContext) -> Document:
    # 兼容旧调用：仅有 filtered_out_count 时拆分
    if ctx.list_filtered_out == 0 and ctx.detail_filtered_out == 0 and ctx.filtered_out_count:
        ctx.list_filtered_out = ctx.filtered_out_count
    if ctx.candidates_count == 0 and ctx.detail_success_count:
        # 粗略回退：无法精确时用 detail - detail_filtered
        ctx.candidates_count = max(
            0, ctx.detail_success_count - ctx.detail_filtered_out
        )
    if ctx.primary_count == 0:
        ctx.primary_count = ctx.final_count
    if not ctx.sources_succeeded and ctx.sources:
        ctx.sources_succeeded = list(ctx.sources)
    if not ctx.sources_requested:
        ctx.sources_requested = list(
            dict.fromkeys(
                list(ctx.sources_succeeded)
                + list((ctx.sources_failed or {}).keys())
            )
        )

    items = _prepare_items(ctx)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(11)
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    pf = style.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE

    _add_header_footer(doc, ctx)
    _page1_cover(doc, ctx, items)
    _page2_summary(doc, ctx, items)
    _page3_overview(doc, ctx, items)
    _detail_pages(doc, ctx, items)
    _last_page(doc, ctx, items)
    return doc


def generate_report_file(
    ctx: ReportContext,
    *,
    reports_dir: Path,
) -> Path:
    """生成 Word 并写入磁盘，返回绝对路径."""
    path = build_report_filename(
        ctx.original_query,
        when=ctx.generated_at,
        reports_dir=reports_dir,
    )
    doc = build_word_document(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path.resolve()
