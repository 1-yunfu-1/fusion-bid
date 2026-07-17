"""标题与文本标准化（去重前处理）."""

from __future__ import annotations

import re
import unicodedata


_BRACKET_RE = re.compile(r"[\[【(（][^\]】)）]*[\]】)）]")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[，,。．.\-—_：:；;！!？?、/\\|]+")
_SUFFIXES = (
    "公开招标公告",
    "竞争性磋商公告",
    "竞争性谈判公告",
    "询价公告",
    "中标公告",
    "成交公告",
    "更正公告",
    "招标公告",
    "采购公告",
    "公告",
)


def normalize_title(title: str) -> str:
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title).strip().lower()
    t = _BRACKET_RE.sub("", t)
    t = _PUNCT_RE.sub("", t)
    t = _SPACE_RE.sub("", t)
    for suf in _SUFFIXES:
        if t.endswith(suf.lower()):
            t = t[: -len(suf)]
            break
    return t


def normalize_bid_code(text: str) -> str | None:
    """从文本中提取可能的项目/公告编号."""
    if not text:
        return None
    patterns = [
        r"(?:项目编号|招标编号|采购编号|公告编号|编号)[:：\s]*([A-Za-z0-9\-_/]{5,40})",
        r"\b([A-Z]{1,6}\d{4,}[\-A-Za-z0-9]{0,20})\b",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1).strip().upper()
    return None


def attachment_name_set(links: list[str] | None) -> set[str]:
    names: set[str] = set()
    for link in links or []:
        name = link.rstrip("/").split("/")[-1].split("?")[0].lower()
        if name:
            names.add(name)
    return names
