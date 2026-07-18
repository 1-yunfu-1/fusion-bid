"""中国招标投标公共服务平台（cebpub）— 公开 JSON 接口，无需登录.

接口形态参考公开资料与 TenderCrawler；本仓异步实现并限速。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from app.cleaners.html_cleaner import clean_html_to_text, extract_attachment_links
from app.browser.pdf_detail import fetch_public_pdf_detail
from app.core.config import get_settings
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
DETAIL_PAGE = f"{CEBPUB_BASE}/searchbusinesstypebeforedooraction/showDetails.do"
DETAIL_API = f"{CEBPUB_BASE}/SecondaryAction/findDetails.do"
CURRENT_DETAIL_BASE = "https://ctbpsp.com/#/bulletinDetail"
TZ = ZoneInfo("Asia/Shanghai")


def current_detail_url(business_id: str) -> str:
    """Map the legacy public-list ID to the current official detail route."""

    value = str(business_id or "").strip()
    if not value:
        return "https://ctbpsp.com/"
    return f"{CURRENT_DETAIL_BASE}?uuid={value}&inpvalue=&dataSource=0&tenderAgency="


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


def _metadata_content(item: ListItem) -> str:
    """Build a truthful fallback solely from the official search response."""
    raw = item.raw or {}
    parts = [
        f"项目名称：{item.title}",
        f"区域：{item.region or '原文未明确说明'}",
        f"交易平台：{raw.get('transactionPlatfName') or '原文未明确说明'}",
        f"项目编号：{raw.get('tenderProjectCode') or '原文未明确说明'}",
        f"行业：{raw.get('industriesType') or '原文未明确说明'}",
        f"公告发布时间：{raw.get('receiveTime') or '原文未明确说明'}",
        f"公告结束时间：{raw.get('bulletinEndTime') or '原文未明确说明'}",
        "说明：本条仅使用公开检索接口元数据；完整正文以官方公告详情为准。",
    ]
    return "\n".join(parts)


def _norm(value: Any) -> str:
    return re.sub(r"\W+", "", str(value or ""))


def _find_current_detail(
    payload: Any, *, business_id: str, title: str
) -> dict[str, Any] | None:
    """Select only a detail object that can be tied to the requested list item.

    Some versions of the official response place ID/title on an outer wrapper
    and bulletin fields in a nested object.  Identity may therefore be
    inherited only from an already verified parent in the same JSON response.
    """
    title_probe = _norm(title)[:16]

    def walk(value: Any, verified_parent: bool = False) -> dict[str, Any] | None:
        if isinstance(value, list):
            for child in value:
                found = walk(child, verified_parent)
                if found:
                    return found
            return None
        if not isinstance(value, dict):
            return None
        node_id = str(
            value.get("businessId")
            or value.get("businessID")
            or value.get("id")
            or ""
        )
        node_title = _norm(
            value.get("businessObjectName")
            or value.get("title")
            or value.get("projectName")
            or value.get("tenderProjectName")
            or ""
        )
        identity_verified = verified_parent or (
            bool(business_id and node_id == business_id)
            and bool(title_probe and len(title_probe) >= 6 and title_probe in node_title)
        )
        if identity_verified and any(
            key in value
            for key in (
                "bulletinContent",
                "content",
                "noticeContent",
                "htmlContent",
                "tendererName",
                "tenderAgencyName",
                "qualificationRequirements",
            )
        ):
            return value
        for child in value.values():
            found = walk(child, identity_verified)
            if found:
                return found
        return None

    return walk(payload)


def _value(node: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _build_detail_content(item: ListItem, detail: dict[str, Any]) -> str:
    """Convert verified official fields into auditable extraction text."""
    raw = item.raw or {}
    labels = [
        ("项目名称", _value(detail, "businessObjectName", "projectName", "tenderProjectName") or item.title),
        ("招标人名称", _value(detail, "tendererName", "tenderName", "purchaserName", "purchaser")),
        ("招标代理机构", _value(detail, "tenderAgencyName", "agencyName", "tenderAgentName")),
        ("项目编号", _value(detail, "tenderProjectCode", "projectCode", "tenderCode") or _value(raw, "tenderProjectCode")),
        ("公告发布时间", _value(detail, "receiveTime", "publishTime", "bulletinIssueTime") or _value(raw, "receiveTime")),
        ("公告结束时间", _value(detail, "bulletinEndTime", "endTime") or _value(raw, "bulletinEndTime")),
        ("投标人资格要求", _value(detail, "qualificationRequirements", "qualificationRequirement", "qualification")),
    ]
    parts = [f"{label}：{value}" for label, value in labels if value]
    body_html = _value(detail, "bulletinContent", "content", "noticeContent", "htmlContent")
    body = clean_html_to_text(body_html) if body_html else ""
    if body:
        parts.extend(["公告正文：", body[:30000]])
    if not body and not any(value for _, value in labels[1:]):
        return ""
    return "\n".join(parts)


def _verified_attachment_urls(payload: Any) -> list[str]:
    """Read URLs only from explicitly named attachment/file fields in a verified payload."""
    links: list[str] = []

    def visit(value: Any, attachment_context: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                is_attachment_key = bool(
                    re.search(r"attachment|annex|accessory|file|附件|附档", str(key), re.I)
                )
                visit(child, attachment_context or is_attachment_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, attachment_context)
        elif attachment_context and isinstance(value, str):
            candidate = value.strip()
            if re.match(r"^https?://", candidate) and candidate not in links:
                links.append(candidate)

    visit(payload)
    return links


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
            # 旧公开列表 businessId 可直接映射到现行官方详情 uuid。
            url = current_detail_url(bid)
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

    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
        """通过官方详情链路读取当前公告；失败时严格降级为列表元数据。"""
        raw = item.raw or {}
        clean = _metadata_content(item)
        business_id = str(raw.get("businessId") or item.source_item_id or "").strip()
        detail_url = current_detail_url(business_id) if business_id else item.source_url
        # 真实平台 ID 为 32 位十六进制字符。此路径等待 PDF.js 文本层，
        # 避免把入口页、广告或同类项目内容当作公告正文。
        if re.fullmatch(r"[0-9a-fA-F]{32}", business_id):
            settings = get_settings()
            managed = settings.cebpub_browser_mode == "managed"
            pdf_detail = await fetch_public_pdf_detail(
                detail_url=detail_url,
                expected_id=business_id,
                expected_title=item.title,
                timeout_ms=(
                    300_000
                    if interactive
                    else settings.cebpub_browser_timeout_seconds * 1_000
                ),
                headless=not interactive,
                managed=managed,
            )
            pdf_metadata = {
                "detail_status": pdf_detail.status,
                "detail_url": detail_url,
                "pdf_url": pdf_detail.pdf_url,
                "business_id": business_id,
                "tender_project_code": raw.get("tenderProjectCode") or None,
                "verified_by": "official_origin+uuid+page_title+pdf_project_title_core+complete_pages",
                "content_format": pdf_detail.content_format,
                "content_pages": pdf_detail.pages,
                "message": pdf_detail.message,
                "failure_reason": pdf_detail.failure_reason,
                "acquisition_mode": pdf_detail.acquisition_mode
                or (
                    "managed_chrome"
                    if managed
                    else ("interactive" if interactive else "headless")
                ),
                "browser_reused": pdf_detail.browser_reused,
                "browser_state": pdf_detail.browser_state,
                "interaction_requested": interactive,
            }
            if pdf_detail.status == "full":
                raw_json = json.dumps(
                    {"pages": pdf_detail.pages, "pdf_url": pdf_detail.pdf_url},
                    ensure_ascii=False,
                )
                return DetailResult(
                    title=item.title,
                    source_url=detail_url,
                    publish_time=item.publish_time,
                    region=item.region,
                    raw_content=raw_json[:1_500_000],
                    clean_content=pdf_detail.clean_content[:1_000_000],
                    attachment_links=[],
                    detail_fetched=True,
                    detail_status="full",
                    detail_url=detail_url,
                    content_format=pdf_detail.content_format,
                    source_metadata=pdf_metadata,
                    raw={
                        **raw,
                        "detail_fetched": True,
                        "detail_status": "full",
                        "content_pages": pdf_detail.pages,
                    },
                )
            # 32 位 UUID 的现行详情只信任专用浏览器中已校验的 PDF。
            # 即使本次仅得到 metadata_only，也必须保留浏览器的真实失败原因，
            # 不能再被旧详情接口的空响应覆盖。
            return DetailResult(
                title=item.title,
                source_url=detail_url,
                publish_time=item.publish_time,
                region=item.region,
                raw_content=clean,
                clean_content=clean,
                attachment_links=[],
                detail_fetched=False,
                detail_status=pdf_detail.status,
                detail_url=detail_url,
                content_format=pdf_detail.content_format,
                source_metadata=pdf_metadata,
                raw={
                    **raw,
                    "detail_fetched": False,
                    "detail_status": pdf_detail.status,
                },
            )
        detail_form = {
            "schemaVersion": raw.get("schemaVersion") or "V60.02",
            "businessKeyWord": "tenderBulletin",
            "tenderProjectCode": raw.get("tenderProjectCode") or "",
            "businessId": business_id,
            "businessObjectName": item.title,
            "transactionPlatfName": raw.get("transactionPlatfName") or "",
            "platformCode": raw.get("transactionPlatfCode") or raw.get("platformCode") or "",
            "oldBusinessId": raw.get("oldBusinessId") or business_id,
        }
        api_form = {
            "schemaVersion": detail_form["schemaVersion"],
            "businessKeyWord": "tenderBulletin",
            "tenderProjectCode": detail_form["tenderProjectCode"],
            "businessObjectName": item.title,
            "businessId": business_id,
        }
        metadata = {
            "detail_status": "metadata_only",
            "detail_endpoint": DETAIL_API,
            "business_id": business_id or None,
            "tender_project_code": detail_form["tenderProjectCode"] or None,
            "verified_by": "official_detail_chain",
            "detail_url": detail_url,
        }
        try:
            responses = await self.fetcher.post_form_sequence(
                [(DETAIL_PAGE, detail_form), (DETAIL_API, api_form)]
            )
            page_text = getattr(responses[0], "text", "") or ""
            page_probe = _norm(page_text)
            title_probe = _norm(item.title)[:16]
            page_matches = bool(
                business_id
                and business_id in page_text
                and title_probe
                and title_probe in page_probe
            )
            detail_payload = responses[1].json()
            current = _find_current_detail(
                detail_payload, business_id=business_id, title=item.title
            )
            # JSON 节点已经以 ID + 标题双锚点校验；页面文本仅作额外审计信息。
            if current:
                full_clean = _build_detail_content(item, current)
                if full_clean:
                    raw_json = json.dumps(current, ensure_ascii=False, default=str)
                    metadata.update(
                        {
                            "detail_status": "full",
                            "detail_fetched": True,
                            "verified_title": current.get("businessObjectName")
                            or current.get("title")
                            or item.title,
                            "page_matches": page_matches,
                        }
                    )
                    return DetailResult(
                        title=item.title,
                        source_url=detail_url,
                        publish_time=item.publish_time,
                        region=item.region,
                        raw_content=raw_json[:500000],
                        clean_content=full_clean,
                        attachment_links=[],
                        detail_fetched=True,
                        detail_status="full",
                        detail_url=detail_url,
                        content_format="json",
                        source_metadata=metadata,
                        raw={
                            **raw,
                            "detail_fetched": True,
                            "detail_status": "full",
                            "detail_payload": current,
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            logger.info("cebpub detail unavailable for %s: %s", business_id or item.title, exc)
        return DetailResult(
            title=item.title,
            source_url=detail_url,
            publish_time=item.publish_time,
            region=item.region,
            raw_content=clean,
            clean_content=clean,
            attachment_links=[],
            detail_fetched=False,
            detail_status="metadata_only",
            detail_url=detail_url,
            content_format=None,
            source_metadata=metadata,
            raw={**raw, "detail_fetched": False, "detail_status": "metadata_only"},
        )

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        if detail.attachment_links:
            return list(detail.attachment_links)
        if not detail.detail_fetched:
            return []
        payload = (detail.raw or {}).get("detail_payload")
        links = _verified_attachment_urls(payload)
        if isinstance(payload, dict):
            body_html = _value(
                payload, "bulletinContent", "content", "noticeContent", "htmlContent"
            )
            for link in extract_attachment_links(body_html, base_url=detail.source_url):
                if link not in links:
                    links.append(link)
        return links
