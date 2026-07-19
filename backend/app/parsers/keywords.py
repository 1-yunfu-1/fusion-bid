"""主题/关键词抽取（规则法，避免对验收句硬编码答案）."""

from __future__ import annotations

import re

from app.parsers.regions import strip_regions

# 噪声词与句式填充
_STOP_PHRASES = [
    "最近",
    "我想知道",
    "帮我",
    "看看",
    "请",
    "汇总",
    "整理",
    "成报告",
    "生成报告",
    "报告",
    "发送给我",
    "发给我",
    "推送",
    "通知我",
    "都有哪些",
    "有哪些",
    "有什么",
    "哪些",
    "什么",
    "相关",
    "有关",
    "区域",
    "内的",
    "的",
    "和",
    "或",
    "与",
    "在",
    "到",
    "为",
    "了",
    "吗",
    "呢",
    "吧",
    "一下",
    "信息",
    "公告",
    "项目",
    "每天",
    "每日",
    "每周",
    "每月",
    "今天",
    "明天",
    "上午",
    "下午",
    "早上",
    "晚上",
    "中午",
]

_NOISE_RE = re.compile(
    r"(招标|采购|投标|中标|询价|竞价|比选|招募|信息|公告|项目|需求|"
    r"最近|帮我|看看|请|汇总|整理|报告|发送|推送|通知|都有哪些|有哪些|"
    r"区域|内|的|和|与|在|关于|有关|相关|建设有关)"
)

# 显式模式：…的XX招标 / 和XX有关 / 关于XX
_EXPLICIT_PATTERNS = [
    # 核电或核能相关 / 服务器、存储相关
    re.compile(r"([\u4e00-\u9fffA-Za-z0-9、或]{2,40}?)\s*(?:有关|相关|方面)"),
    # 和充电设施建设有关 / 与XX相关
    re.compile(r"(?:和|与|关于)\s*([\u4e00-\u9fffA-Za-z0-9]{2,24}?)\s*(?:有关|相关|方面)"),
    re.compile(r"关于\s*([\u4e00-\u9fffA-Za-z0-9]{2,24})"),
    # 紧邻「招标/采购」的中文主题（优先短窗口）
    re.compile(r"([\u4e00-\u9fff]{2,12})(?:招标|采购|投标)"),
    re.compile(r"(?:采购|招标)\s*([\u4e00-\u9fffA-Za-z0-9]{2,20})"),
]


def _clean_candidate(token: str) -> str:
    t = token.strip(" \t\n\r的了与和及、，,。．.；;：:")
    t = re.sub(r"^(和|与|关于|有关)", "", t)
    t = re.sub(r"(有关|相关|方面|建设有关)$", "", t)
    # 取「的」后最后一段，避免「区域内的服务器」整段入选
    if "的" in t:
        t = t.split("的")[-1]
    t = t.strip()
    for p in _STOP_PHRASES:
        if t == p:
            return ""
    return t


def _accept_keyword(cand: str) -> bool:
    if not cand or len(cand) < 2:
        return False
    if re.fullmatch(r"\d+", cand):
        return False
    if re.fullmatch(r"\d{1,2}月(份)?", cand):
        return False
    if "区域" in cand or cand.endswith("内的"):
        return False
    noise = {
        "信息都",
        "信息都有哪些",
        "都有哪些",
        "有哪些",
        "招标信息",
        "采购信息",
        "整理成",
        "帮我看",
        "区域内",
    }
    if cand in noise or cand.endswith("都有哪些") or cand.endswith("有哪些"):
        return False
    return True


def extract_keywords(text: str) -> list[str]:
    """提取主题关键词列表."""
    keywords: list[str] = []
    # 去区域后再抽，减少「区域内的服务器」整段误匹配
    corpus = [text, strip_regions(text)]

    # 1) 显式模式优先
    for src in corpus:
        for pattern in _EXPLICIT_PATTERNS:
            for m in pattern.finditer(src):
                cand = _clean_candidate(strip_regions(m.group(1)))
                for part in re.split(r"(?:或者|或|、)", cand):
                    part = _clean_candidate(part)
                    if _accept_keyword(part) and part not in keywords:
                        keywords.append(part)

    if keywords:
        return _dedupe_prefer_longer(keywords)

    # 2) 去区域、时间、句式后取剩余实质片段
    residual = strip_regions(text)
    residual = re.sub(
        r"(最近|近|过去)\s*(?:\d+|[一二三四五六七八九十两]+)\s*(年|个月|月|周|星期|天|日)",
        " ",
        residual,
    )
    residual = re.sub(r"20\d{2}\s*年", " ", residual)
    residual = re.sub(r"\d{1,2}\s*月(份)?", " ", residual)
    residual = re.sub(r"(每天|每日|每周|每月|今天|明天|今日|明日)", " ", residual)
    residual = re.sub(r"\d{1,2}\s*[点时:]\s*\d{0,2}\s*分?", " ", residual)
    residual = re.sub(r"\d{1,2}:\d{2}", " ", residual)
    residual = re.sub(
        r"(请)?汇总后|(发送|推送|发给)(给我)?|整理成报告|生成报告|帮我看看|都有哪些|有哪些",
        " ",
        residual,
    )
    residual = re.sub(r"[，,。．.、；;：:\s]+", " ", residual)

    # 去掉招标等业务虚词后的连续中文块
    # 若显式模式已命中主题，不再用残余碎片污染关键词
    if keywords:
        return _dedupe_prefer_longer(keywords)

    parts = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}", residual)
    for p in parts:
        cleaned = _NOISE_RE.sub("", p)
        cleaned = _clean_candidate(cleaned)
        if cleaned and len(cleaned) >= 2 and cleaned not in keywords:
            if cleaned in ("区域内", "信息都", "建设有") or "哪些" in cleaned:
                continue
            keywords.append(cleaned)

    return _dedupe_prefer_longer(keywords)


def _dedupe_prefer_longer(items: list[str]) -> list[str]:
    """去重：若短词是长词子串则保留长词."""
    items = sorted(set(items), key=len, reverse=True)
    kept: list[str] = []
    for item in items:
        if any(item != k and item in k for k in kept):
            continue
        kept.append(item)
    # 恢复较自然顺序：短到长不稳定，按原相对长度
    return list(reversed(kept)) if len(kept) > 1 else kept


def extract_exclude_keywords(text: str) -> list[str]:
    """排除词：不含/排除/除了 …"""
    found: list[str] = []
    for m in re.finditer(r"(?:不含|排除|除了|不要)\s*([^\s,，。、]{2,20})", text):
        cand = _clean_candidate(m.group(1))
        if cand:
            found.append(cand)
    return found
