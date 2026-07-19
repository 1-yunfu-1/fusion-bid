"""CCGP 列表解析单元测试（使用本地 HTML fixture，不访问外网）."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from app.cleaners.filters import FilterContext, filter_detail, filter_list_item
from app.cleaners.html_cleaner import clean_html_to_text, extract_attachment_links
from app.sources.base import DetailResult, ListItem, SearchQuery
from app.sources.ccgp_source import CcgpSource, _parse_publish, _region_from_list_part


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


def test_region_parser_rejects_purchaser_label_containing_province_name():
    assert _region_from_list_part("采购人：安徽省产品质量监督检验研究院") is None
    assert _region_from_list_part("代理机构：安徽省招标集团股份有限公司") is None
    assert _region_from_list_part("安徽省") == "安徽省"
    assert _region_from_list_part("行政区域：合肥市") == "合肥市"


def test_clean_and_attachments():
    text = clean_html_to_text(SAMPLE_DETAIL_HTML)
    assert "网站导航" not in text
    assert "页脚版权" not in text
    assert "采购人" in text
    links = extract_attachment_links(
        SAMPLE_DETAIL_HTML, base_url="http://www.ccgp.gov.cn/cggg/x.htm"
    )
    assert any(x.endswith(".pdf") for x in links)


def test_cleaner_survives_descendants_of_decomposed_nodes():
    html = """
    <html><body>
      <script><span class="menu">invalid child</span></script>
      <iframe><div id="header">invalid frame child</div></iframe>
      <div class="vF_detail_content">
        <h1>服务器采购公告</h1>
        <p>采购人：测试单位</p>
        <p>资格要求：具备独立法人资格并具有履约能力。</p>
      </div>
    </body></html>
    """

    text = clean_html_to_text(html)

    assert "invalid child" not in text
    assert "invalid frame child" not in text
    assert "采购人：测试单位" in text


def test_cleaner_preserves_ccgp_article_wrapped_in_large_form():
    paragraphs = "".join(
        f"<p>{index}. 公告正文：服务器采购项目资格与成交信息。</p>"
        for index in range(20)
    )
    html = f"""
    <html><body>
      <div class="vF_detail_content_container">
        <div class="vF_detail_main">
          <div class="table">
            <table><tr><td>采购单位</td><td>测试采购单位</td></tr></table>
          </div>
          <div class="vF_detail_content">
            <form id="aspnetForm">
              <h1>服务器采购项目结果公告</h1>
              {paragraphs}
            </form>
          </div>
        </div>
      </div>
      <form class="search"><input name="keyword"><button>搜索</button></form>
    </body></html>
    """

    text = clean_html_to_text(html)

    assert "服务器采购项目结果公告" in text
    assert "采购单位\n测试采购单位" in text
    assert len(text) > 200
    assert "搜索" not in text


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


def test_nationwide_filter_accepts_multiple_regions_and_or_keywords():
    ctx = FilterContext(
        keywords=["核电", "核能"],
        regions=["全国"],
        start_date=None,
        end_date=None,
    )
    for region, keyword in (("北京市", "核电"), ("安徽省", "核能"), ("广东省", "核电")):
        item = ListItem(
            title=f"{region}{keyword}设备公开招标公告",
            source_url=f"https://example.com/{region}",
            region=region,
        )
        detail = DetailResult(
            title=item.title,
            source_url=item.source_url,
            region=region,
            clean_content=f"{region} {keyword}设备采购 招标人：测试单位",
        )
        assert filter_list_item(item, ctx).accepted is True
        assert filter_detail(detail, ctx).accepted is True

    restricted = FilterContext(
        keywords=["核电", "核能"],
        regions=["安徽省"],
        start_date=None,
        end_date=None,
    )
    beijing = ListItem(
        title="北京市核电设备公开招标公告",
        source_url="https://example.com/beijing",
        region="北京市",
    )
    assert filter_list_item(beijing, restricted).accepted is False


@pytest.mark.asyncio
async def test_ccgp_search_with_mocked_html(monkeypatch):
    source = CcgpSource()
    source.max_pages = 1

    async def fake_get_text(url, params=None):
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(source.fetcher, "get_text", fake_get_text)
    items = await source.search(
        SearchQuery(keywords=["服务器"], regions=["安徽省"], start_date="2026-03-01", end_date="2026-03-31")
    )
    assert len(items) >= 1
    assert any("服务器" in i.title for i in items)


@pytest.mark.asyncio
async def test_ccgp_nationwide_search_omits_display_zone(monkeypatch):
    source = CcgpSource()
    source.max_pages = 1
    captured: dict = {}

    async def fake_get_text(url, params=None):
        captured.update(params or {})
        return '<html><div class="vT-srch-result"></div></html>'

    monkeypatch.setattr(source.fetcher, "get_text", fake_get_text)
    await source.search(SearchQuery(keywords=["核电"], regions=["全国"]))
    assert captured["displayZone"] == ""
