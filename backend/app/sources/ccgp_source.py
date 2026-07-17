"""中国政府采购网（search.ccgp.gov.cn）— 公开源，无需登录.

解析逻辑参考开源 TenderCrawler 的公开实现思路，本仓异步重写；
遵守限速，不绕过反爬。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.cleaners.html_cleaner import clean_html_to_text, extract_attachment_links
from app.sources.base import (
    DetailResult,
    HealthResult,
    ListItem,
    SearchQuery,
    TenderSourceAdapter,
)
from app.sources.http_util import HttpFetcher

logger = logging.getLogger(__name__)

SEARCH_URL = "https://search.ccgp.gov.cn/bxsearch"
HOME_URL = "https://www.ccgp.gov.cn/"
RATE_LIMIT_MARKERS = ("您的访问过于频繁", "频繁访问")
TZ = ZoneInfo("Asia/Shanghai")


def _ccgp_date(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().replace("-", ":")


def _parse_publish(text: str) -> datetime | None:
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TZ)
    except ValueError:
        return None


class CcgpSource(TenderSourceAdapter):
    source_name = "ccgp"
    display_name = "中国政府采购网"
    requires_login = False
    enabled = True
    official = True

    def __init__(self) -> None:
        # 采购网较慢，默认间隔 ≥4 秒
        self.fetcher = HttpFetcher(
            timeout=60.0,
            min_interval=4.0,
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        self.max_pages = 2  # 演示与合规：限制页数，避免高频

    async def health_check(self) -> HealthResult:
        try:
            html = await self.fetcher.get_text(HOME_URL)
            ok = "政府采购" in html or "ccgp" in html.lower()
            return HealthResult(
                ok=ok,
                message="中国政府采购网可达" if ok else "首页响应异常，页面结构可能变化",
                requires_login=False,
                checked_at=datetime.now(TZ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ccgp health failed: %s", exc)
            return HealthResult(
                ok=False,
                message=f"健康检查失败: {exc}",
                requires_login=False,
                checked_at=datetime.now(TZ),
            )

    async def search(self, query: SearchQuery) -> list[ListItem]:
        keywords = query.keywords or [""]
        items: list[ListItem] = []
        seen: set[str] = set()
        for kw in keywords[:3]:
            for page in range(1, self.max_pages + 1):
                page_items = await self._fetch_list_page(kw, query, page)
                if not page_items:
                    break
                for it in page_items:
                    key = it.source_url or it.title
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(it)
        return items

    async def _fetch_list_page(
        self, keyword: str, query: SearchQuery, page_no: int
    ) -> list[ListItem]:
        params = {
            "searchtype": "1",
            "page_index": str(page_no),
            "bidSort": "0",
            "buyerName": "",
            "projectId": "",
            "pinMu": "0",
            "bidType": "0",
            "dbselect": "bidx",
            "kw": keyword,
            "start_time": _ccgp_date(query.start_date),
            "end_time": _ccgp_date(query.end_date),
            "timeType": "6",
            "displayZone": "",
            "zoneId": "",
            "pppStatus": "0",
            "agentName": "",
        }
        # 区域：放入 displayZone 文本检索辅助
        if query.regions:
            params["displayZone"] = query.regions[0].replace("省", "").replace("市", "")

        try:
            html = await self.fetcher.get_text(SEARCH_URL, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.error("ccgp search failed kw=%s page=%s: %s", keyword, page_no, exc)
            raise RuntimeError(f"中国政府采购网检索失败: {exc}") from exc

        if any(m in html for m in RATE_LIMIT_MARKERS):
            raise RuntimeError("中国政府采购网提示访问过于频繁，请增大间隔后重试")

        soup = BeautifulSoup(html, "lxml")
        lis = soup.select(".vT-srch-result-list-bid li")
        if not lis and "vT-srch-result" not in html:
            logger.warning("ccgp page structure unexpected")
            raise RuntimeError("中国政府采购网列表页结构异常，选择器可能已失效")

        results: list[ListItem] = []
        for li in lis:
            a = li.select_one("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = (a.get("href") or "").strip()
            if not title or not href:
                continue
            url = href if href.startswith("http") else urljoin("http://www.ccgp.gov.cn", href)
            region = ""
            publish = None
            span = li.select_one("span")
            if span:
                parts = [p.strip() for p in span.get_text().split("|") if p.strip()]
                if parts:
                    publish = _parse_publish(parts[0])
                for part in parts[1:]:
                    if any(x in part for x in ("省", "市", "区", "县")) and len(part) < 40:
                        region = part
                        break
            item_id = hashlib.md5(url.encode("utf-8")).hexdigest()
            results.append(
                ListItem(
                    title=title,
                    source_url=url,
                    source_item_id=item_id,
                    publish_time=publish,
                    snippet=li.get_text(" ", strip=True)[:300],
                    region=region or None,
                    raw={"keyword": keyword, "source": "ccgp"},
                )
            )
        return results

    async def fetch_detail(self, item: ListItem) -> DetailResult:
        try:
            html = await self.fetcher.get_text(item.source_url)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"详情抓取失败: {exc}") from exc
        clean = clean_html_to_text(html)
        atts = extract_attachment_links(html, base_url=item.source_url)
        pub = item.publish_time
        # 尝试从正文提时间
        if pub is None:
            pub = _parse_publish(clean[:500])
        return DetailResult(
            title=item.title,
            source_url=item.source_url,
            publish_time=pub,
            region=item.region,
            raw_content=html[:200000],
            clean_content=clean,
            attachment_links=atts,
            raw={"source": "ccgp"},
        )

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        if detail.attachment_links:
            return detail.attachment_links
        return extract_attachment_links(detail.raw_content, base_url=detail.source_url)
