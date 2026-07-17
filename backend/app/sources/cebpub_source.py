"""中国招标投标公共服务平台（cebpub）— 公开 JSON 接口，无需登录.

接口形态参考公开资料与 TenderCrawler；本仓异步实现并限速。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

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

CEBPUB_BASE = "http://www.cebpubservice.com/ctpsp_iiss"
SEARCH_API = f"{CEBPUB_BASE}/searchbusinesstypebeforedooraction/getStringMethod.do"
PORTAL = f"{CEBPUB_BASE}/searchbusinesstypebeforedooraction/getSearch.do"
TZ = ZoneInfo("Asia/Shanghai")


def _parse_date(value: str | None) -> datetime | None:
    if not value or value.startswith("1970"):
        return None
    m = re.match(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TZ)
    except ValueError:
        return None


class CebpubSource(TenderSourceAdapter):
    source_name = "cebpub"
    display_name = "中国招标投标公共服务平台"
    requires_login = False
    enabled = True
    official = True

    def __init__(self) -> None:
        self.fetcher = HttpFetcher(
            timeout=45.0,
            min_interval=1.0,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": PORTAL,
            },
        )
        self.max_pages = 2
        self.page_size = 20

    async def health_check(self) -> HealthResult:
        try:
            resp = await self.fetcher.post_form(
                SEARCH_API,
                {
                    "searchName": "服务器",
                    "searchArea": "",
                    "searchIndustry": "",
                    "centerPlat": "",
                    "businessType": "招标公告",
                    "searchTimeStart": "",
                    "searchTimeStop": "",
                    "timeTypeParam": "",
                    "bulletinIssnTime": "",
                    "bulletinIssnTimeStart": "",
                    "bulletinIssnTimeStop": "",
                    "pageNo": 1,
                    "row": 1,
                },
            )
            body = resp.json()
            ok = bool(body.get("success")) or "object" in body
            return HealthResult(
                ok=ok,
                message="cebpub 检索接口可达" if ok else f"接口响应异常: {body.get('message')}",
                requires_login=False,
                checked_at=datetime.now(TZ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cebpub health failed: %s", exc)
            return HealthResult(
                ok=False,
                message=f"健康检查失败: {exc}",
                requires_login=False,
                checked_at=datetime.now(TZ),
            )

    async def search(self, query: SearchQuery) -> list[ListItem]:
        keywords = query.keywords or [""]
        area = ""
        if query.regions:
            area = query.regions[0].replace("省", "").replace("市", "")
        items: list[ListItem] = []
        seen: set[str] = set()
        for kw in keywords[:3]:
            for page in range(1, self.max_pages + 1):
                batch = await self._fetch_page(kw, query, page, area)
                if not batch:
                    break
                for it in batch:
                    key = it.source_item_id or it.title
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(it)
        return items

    async def _fetch_page(
        self, keyword: str, query: SearchQuery, page_no: int, area: str
    ) -> list[ListItem]:
        data = {
            "searchName": keyword,
            "searchArea": area,
            "searchIndustry": "",
            "centerPlat": "",
            "businessType": "招标公告",
            "searchTimeStart": query.start_date or "",
            "searchTimeStop": query.end_date or "",
            "timeTypeParam": "",
            "bulletinIssnTime": "",
            "bulletinIssnTimeStart": "",
            "bulletinIssnTimeStop": "",
            "pageNo": page_no,
            "row": self.page_size,
        }
        try:
            resp = await self.fetcher.post_form(SEARCH_API, data)
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("cebpub search failed: %s", exc)
            raise RuntimeError(f"招标投标公共服务平台检索失败: {exc}") from exc

        if body.get("success") is False and body.get("object") is None:
            msg = body.get("message") or "未知错误"
            # 空结果不一定是失败
            if "无" in str(msg):
                return []
            raise RuntimeError(f"cebpub 查询失败: {msg}")

        obj = body.get("object") or {}
        raw_list = obj.get("returnlist") or []
        results: list[ListItem] = []
        for raw in raw_list:
            title = (raw.get("businessObjectName") or "").strip()
            if not title:
                continue
            bid = str(raw.get("businessId") or "")
            # 列表未必给详情 URL；用检索页 + businessId 作为可追溯标识 URL
            url = f"{PORTAL}?businessId={bid}" if bid else PORTAL
            region = (raw.get("regionName") or "").strip() or None
            pub = _parse_date(raw.get("receiveTime"))
            results.append(
                ListItem(
                    title=title,
                    source_url=url,
                    source_item_id=bid or None,
                    publish_time=pub,
                    snippet=(raw.get("transactionPlatfName") or "")[:200],
                    region=region,
                    raw={**raw, "keyword": keyword, "source": "cebpub"},
                )
            )
        return results

    async def fetch_detail(self, item: ListItem) -> DetailResult:
        """列表已含关键字段；尝试拉取检索页 HTML 作补充，失败则用列表信息."""
        raw = item.raw or {}
        parts = [
            f"项目名称：{item.title}",
            f"区域：{item.region or '原文未明确说明'}",
            f"交易平台：{raw.get('transactionPlatfName') or '原文未明确说明'}",
            f"项目编号：{raw.get('tenderProjectCode') or '原文未明确说明'}",
            f"行业：{raw.get('industriesType') or '原文未明确说明'}",
            f"公告发布时间：{raw.get('receiveTime') or '原文未明确说明'}",
            f"公告结束时间：{raw.get('bulletinEndTime') or '原文未明确说明'}",
            "说明：详情来自公开检索接口字段；完整正文以官方门户为准。",
        ]
        clean = "\n".join(parts)
        html = ""
        try:
            html = await self.fetcher.get_text(PORTAL)
            extra = clean_html_to_text(html)
            if extra and len(extra) > 50:
                clean = clean + "\n\n" + extra[:3000]
        except Exception:  # noqa: BLE001
            pass
        return DetailResult(
            title=item.title,
            source_url=item.source_url,
            publish_time=item.publish_time,
            region=item.region,
            raw_content=html[:50000] if html else clean,
            clean_content=clean,
            attachment_links=[],
            raw=raw,
        )

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        if detail.attachment_links:
            return list(detail.attachment_links)
        return extract_attachment_links(detail.raw_content or "", base_url=detail.source_url)
