"""CCGP 列表解析单元测试（使用本地 HTML fixture，不访问外网）."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from app.cleaners.filters import FilterContext, filter_list_item
from app.cleaners.html_cleaner import clean_html_to_text, extract_attachment_links
from app.sources.base import ListItem
from app.sources.ccgp_source import CcgpSource, _parse_publish


SAMPLE_LIST_HTML = """
<html><body>
<ul class="vT-srch-result-list-bid">
  <li>
    <a href="http://www.ccgp.gov.cn/cggg/dfgg/gkzb/202603/t20260315_xx.htm">
      安徽省某某单位服务器采购公开招标公告
    </a>
    <span>2026.03.15 00:00:00|安徽省|公开招标公告|采购人：某某单位</span>
  </li>
  <li>
    <a href="/cggg/other.htm">无关培训班报名通知</a>
    <span>2026.03.10 00:00:00|上海市</span>
  </li>
</ul>
<p>共 2 条</p>
</body></html>
"""

SAMPLE_DETAIL_HTML = """
<html><body>
<nav>网站导航</nav>
<div class="vF_detail_content">
  <h1>服务器采购公开招标公告</h1>
  <p>采购人：某某单位</p>
  <p>预算金额：原文未在本测试中断言</p>
  <p>项目所在地：安徽省合肥市</p>
  <a href="/files/a.pdf">附件下载：招标文件.pdf</a>
</div>
<footer>页脚版权</footer>
</body></html>
"""


def test_parse_list_structure():
    soup = BeautifulSoup(SAMPLE_LIST_HTML, "lxml")
    lis = soup.select(".vT-srch-result-list-bid li")
    assert len(lis) == 2
    a = lis[0].select_one("a")
    assert "服务器" in a.get_text()
    pub = _parse_publish(lis[0].select_one("span").get_text())
    assert pub is not None
    assert pub.year == 2026


def test_clean_and_attachments():
    text = clean_html_to_text(SAMPLE_DETAIL_HTML)
    assert "网站导航" not in text
    assert "页脚版权" not in text
    assert "采购人" in text
    links = extract_attachment_links(
        SAMPLE_DETAIL_HTML, base_url="http://www.ccgp.gov.cn/cggg/x.htm"
    )
    assert any(x.endswith(".pdf") for x in links)


def test_filter_list_item_keyword_region():
    item = ListItem(
        title="安徽省某某单位服务器采购公开招标公告",
        source_url="http://example.com/1",
        region="安徽省",
        publish_time=_parse_publish("2026.03.15"),
    )
    ctx = FilterContext(
        keywords=["服务器"],
        regions=["安徽省"],
        start_date=None,
        end_date=None,
    )
    assert filter_list_item(item, ctx).accepted is True
    ctx2 = FilterContext(keywords=["充电桩"], regions=["安徽省"], start_date=None, end_date=None)
    assert filter_list_item(item, ctx2).accepted is False


@pytest.mark.asyncio
async def test_ccgp_search_with_mocked_html(monkeypatch):
    source = CcgpSource()
    source.max_pages = 1

    async def fake_get_text(url, params=None):
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(source.fetcher, "get_text", fake_get_text)
    from app.sources.base import SearchQuery

    items = await source.search(
        SearchQuery(keywords=["服务器"], regions=["安徽省"], start_date="2026-03-01", end_date="2026-03-31")
    )
    assert len(items) >= 1
    assert any("服务器" in i.title for i in items)
