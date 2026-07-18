"""登录态招采门户数据源（Playwright storage state）.

默认对接「中国招标与采购网」公开检索入口；有效会员/登录后可见内容
依赖用户手动登录保存的本地 storage state。

合规：
- 不绕过验证码与登录
- 不保存明文密码
- 仅获取免费账号正常可见信息
- 登录失效时明确提示，不阻塞其他公开源
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.browser.session import (
    BrowserNotAvailableError,
    LoginRequiredError,
    fetch_page_with_state,
    looks_like_login_wall,
    looks_like_logged_in,
    safe_state_meta,
    state_file_path,
    validate_state_file_not_logged,
)
from app.cleaners.html_cleaner import clean_html_to_text, extract_attachment_links
from app.core.config import get_settings
from app.sources.base import (
    DetailResult,
    HealthResult,
    ListItem,
    SearchQuery,
    TenderSourceAdapter,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


def _parse_date(text: str) -> datetime | None:
    m = re.search(r"(20\d{2})[年.\-/](\d{1,2})[月.\-/](\d{1,2})", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TZ)
    except ValueError:
        return None


class LoginPortalSource(TenderSourceAdapter):
    """需要登录后才能稳定获取有效信息的数据源."""

    source_name = "login_portal"
    display_name = "登录态招采门户"
    requires_login = True
    enabled = True
    official = False

    def __init__(self) -> None:
        settings = get_settings()
        self.home_url = settings.login_source_home_url
        self.login_url = settings.login_source_login_url
        self.search_url_template = settings.login_source_search_url
        self.max_items = settings.login_source_max_items
        self.state_path = state_file_path(settings.login_source_state_file)
        hosts = {
            (urlparse(url).hostname or "").lower().removeprefix("www.")
            for url in (self.home_url, self.login_url, self.search_url_template)
        }
        hosts.discard("")
        self.configuration_error = None
        if len(hosts) != 1:
            self.configuration_error = (
                "登录页、首页与检索地址不属于同一门户，登录源已停用；"
                "请统一 LOGIN_SOURCE_HOME_URL、LOGIN_SOURCE_LOGIN_URL 和 "
                "LOGIN_SOURCE_SEARCH_URL。公开源仍会继续执行。"
            )
        self.enabled = bool(settings.login_source_enabled and not self.configuration_error)

    def _build_search_url(self, keyword: str, region: str = "") -> str:
        tpl = self.search_url_template
        return (
            tpl.replace("{keyword}", quote(keyword))
            .replace("{region}", quote(region))
            .replace("{kw}", quote(keyword))
        )

    async def health_check(self) -> HealthResult:
        meta = safe_state_meta(self.state_path)
        if self.configuration_error:
            return HealthResult(
                ok=False,
                message=self.configuration_error,
                requires_login=True,
                login_ok=False,
                checked_at=datetime.now(TZ),
            )
        if not self.enabled:
            return HealthResult(
                ok=False,
                message="登录态数据源已在配置中禁用（LOGIN_SOURCE_ENABLED=false）",
                requires_login=True,
                login_ok=False,
                checked_at=datetime.now(TZ),
            )
        if not meta["exists"]:
            return HealthResult(
                ok=False,
                message=(
                    "未找到采集用登录态文件（data/browser_states/login_portal_state.json）。"
                    "普通 Chrome/Edge 里登录不会同步到本系统；"
                    "请到「数据源」页点击「启动登录浏览器」，"
                    "在弹出窗口登录后按 Enter 保存（或双击 scripts/run_login_init.bat）。"
                ),
                requires_login=True,
                login_ok=False,
                checked_at=datetime.now(TZ),
            )
        try:
            validate_state_file_not_logged(self.state_path)
            html = await fetch_page_with_state(self.home_url, state_path=self.state_path)
        except BrowserNotAvailableError as exc:
            return HealthResult(
                ok=False,
                message=str(exc),
                requires_login=True,
                login_ok=None,
                checked_at=datetime.now(TZ),
            )
        except LoginRequiredError as exc:
            return HealthResult(
                ok=False,
                message=str(exc),
                requires_login=True,
                login_ok=False,
                checked_at=datetime.now(TZ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("login portal health failed: %s", exc)
            return HealthResult(
                ok=False,
                message=f"登录态健康检查失败: {exc}",
                requires_login=True,
                login_ok=None,
                checked_at=datetime.now(TZ),
            )

        if looks_like_login_wall(html):
            return HealthResult(
                ok=False,
                message="登录状态可能已失效（页面出现登录提示），请到「数据源」页重新「启动登录浏览器」",
                requires_login=True,
                login_ok=False,
                checked_at=datetime.now(TZ),
            )
        login_ok = looks_like_logged_in(html) or not looks_like_login_wall(html)
        return HealthResult(
            ok=login_ok,
            message="登录态有效，可尝试抓取" if login_ok else "无法确认登录态，建议重新登录",
            requires_login=True,
            login_ok=login_ok,
            checked_at=datetime.now(TZ),
        )

    async def search(self, query: SearchQuery) -> list[ListItem]:
        if not self.enabled:
            raise LoginRequiredError(self.configuration_error or "登录态数据源未启用")
        if not self.state_path.exists():
            raise LoginRequiredError(
                "登录状态不存在。请到「数据源」页点击「启动登录浏览器」完成手动登录"
            )

        keywords = query.keywords or [""]
        region = (query.regions or [""])[0]
        items: list[ListItem] = []
        seen: set[str] = set()

        for kw in keywords[:2]:
            url = self._build_search_url(kw, region)
            try:
                html = await fetch_page_with_state(url, state_path=self.state_path)
            except BrowserNotAvailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"登录态检索失败: {exc}") from exc

            if looks_like_login_wall(html):
                raise LoginRequiredError(
                    "登录状态失效或无权查看检索结果，请重新手动登录后重试"
                )

            for it in self._parse_list(html, base_url=url, keyword=kw):
                key = it.source_url or it.title
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
                if len(items) >= self.max_items:
                    return items
        return items

    def _parse_list(self, html: str, *, base_url: str, keyword: str) -> list[ListItem]:
        soup = BeautifulSoup(html, "lxml")
        results: list[ListItem] = []
        # 通用列表锚点：标题链接，过滤导航
        candidates = soup.select("a[href]")
        for a in candidates:
            title = a.get_text(" ", strip=True)
            href = (a.get("href") or "").strip()
            if not title or not href or len(title) < 8:
                continue
            if any(x in title for x in ("登录", "注册", "首页", "下载客户端", "APP")):
                continue
            # 招标相关
            if not any(k in title for k in ("招标", "采购", "中标", "询价", "磋商", "投标", keyword)):
                if keyword and keyword not in title:
                    continue
            full = urljoin(base_url, href)
            if not full.startswith("http"):
                continue
            if full.rstrip("/") == self.home_url.rstrip("/"):
                continue
            # 排除站外无关
            parent = a.parent.get_text(" ", strip=True) if a.parent else title
            pub = _parse_date(parent)
            item_id = hashlib.md5(full.encode("utf-8")).hexdigest()[:16]
            results.append(
                ListItem(
                    title=title[:500],
                    source_url=full,
                    source_item_id=item_id,
                    publish_time=pub,
                    snippet=parent[:300],
                    region=None,
                    raw={"keyword": keyword, "source": "login_portal"},
                )
            )
            if len(results) >= self.max_items:
                break
        return results

    async def fetch_detail(
        self, item: ListItem, *, interactive: bool = False
    ) -> DetailResult:
        try:
            html = await fetch_page_with_state(item.source_url, state_path=self.state_path)
        except LoginRequiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"登录态详情失败: {exc}") from exc

        if looks_like_login_wall(html):
            raise LoginRequiredError("详情页需要登录或登录已失效，请重新登录")

        clean = clean_html_to_text(html)
        atts = extract_attachment_links(html, base_url=item.source_url)
        pub = item.publish_time or _parse_date(clean[:800])
        return DetailResult(
            title=item.title,
            source_url=item.source_url,
            publish_time=pub,
            region=item.region,
            raw_content=html[:200000],
            clean_content=clean,
            attachment_links=atts,
            raw={"source": "login_portal", "requires_login": True},
        )

    async def extract_attachments(self, detail: DetailResult) -> list[str]:
        if detail.attachment_links:
            return list(detail.attachment_links)
        return extract_attachment_links(detail.raw_content or "", base_url=detail.source_url)
