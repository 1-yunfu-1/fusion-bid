"""网页正文清洗：去除导航/页脚/广告等噪声."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_DROP_TAGS = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "iframe",
    "noscript",
    "form",
    "button",
)
_DROP_CLASS_KEYWORDS = (
    "nav",
    "menu",
    "footer",
    "header",
    "sidebar",
    "advert",
    "ad-",
    "share",
    "breadcrumb",
    "login",
    "copyright",
    "recommend",
    "related",
    "toolbar",
    "sitenav",
)


def clean_html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class") or []).lower()
        tid = (tag.get("id") or "").lower()
        blob = f"{classes} {tid}"
        if any(k in blob for k in _DROP_CLASS_KEYWORDS):
            tag.decompose()
    # 优先正文容器
    main = (
        soup.select_one("#mainContent")
        or soup.select_one(".vF_detail_content")
        or soup.select_one(".vF_detail_content_container")
        or soup.select_one("#content")
        or soup.select_one(".content")
        or soup.select_one("article")
        or soup.body
        or soup
    )
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_attachment_links(html: str, base_url: str = "") -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        lower = href.lower()
        if any(
            lower.endswith(ext) or ext in lower
            for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".wps")
        ) or any(k in text for k in ("附件", "下载", "招标文件", "采购文件")):
            full = urljoin(base_url, href) if base_url else href
            if full.startswith("http") and full not in seen:
                seen.add(full)
                links.append(full)
    return links
