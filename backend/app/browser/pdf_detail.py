"""公开 PDF.js 公告详情采集。

这里只读取浏览器正常渲染后的公开文本，不填验证码、不注入 Cookie，
也不尝试绕过站点的人机验证。遇到验证页时返回可审计状态，
由上层继续执行其他数据源。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

_VERIFY_MARKERS = (
    "安全验证",
    "人机验证",
    "请完成验证",
    "验证码",
    "拖动滑块",
    "访问过于频繁",
    "captcha",
)


@dataclass
class PublicPdfDetail:
    status: str
    detail_url: str
    content_format: str | None = None
    clean_content: str = ""
    pages: list[dict[str, Any]] = field(default_factory=list)
    pdf_url: str | None = None
    message: str = ""
    failure_reason: str | None = None
    acquisition_mode: str | None = None
    browser_reused: bool = False
    browser_state: str | None = None


def _normalise_identity(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


_DOCUMENT_TITLE_SUFFIXES = (
    "国际招标澄清或变更公告",
    "招标澄清或变更公告",
    "澄清或变更公告",
    "中标候选人公示",
    "资格预审公告",
    "竞争性谈判公告",
    "竞争性磋商公告",
    "中标结果公告",
    "招标公告",
    "采购公告",
    "询价公告",
    "变更公告",
    "更正公告",
    "终止公告",
    "流标公告",
    "废标公告",
    "中标公告",
    "结果公告",
)


def _pdf_content_matches_title(expected_title: str, full_text: str) -> bool:
    """校验 PDF 正文与外层标题的项目主体一致。

    部分官方 PDF 正文从“招标条件”开始，正文只写项目名称，不重复外层
    “招标公告/澄清或变更公告(序号)”后缀。此时仍要求较长的项目主体完整
    出现在 PDF 中；不能因公告类型固定后缀缺失而误判，也不能退化为关键词
    模糊匹配。
    """
    expected = _normalise_identity(expected_title)
    actual = _normalise_identity(full_text)
    if not expected or not actual:
        return False
    if expected in actual:
        return True
    for suffix in _DOCUMENT_TITLE_SUFFIXES:
        normalised_suffix = _normalise_identity(suffix)
        core = re.sub(rf"{re.escape(normalised_suffix)}\d*$", "", expected)
        if core != expected and len(core) >= 8 and core in actual:
            return True
    return False


def _join_pdf_line(items: list[dict[str, Any]]) -> str:
    """按 x 坐标拼接一行，仅在两段都是拉丁文字时补空格。"""
    output = ""
    previous: dict[str, Any] | None = None
    for item in sorted(items, key=lambda row: float(row.get("x") or 0)):
        text = str(item.get("text") or "")
        if not text:
            continue
        if previous and output:
            prev_text = str(previous.get("text") or "")
            prev_end = float(previous.get("x") or 0) + float(previous.get("width") or 0)
            gap = float(item.get("x") or 0) - prev_end
            if (
                gap > 1.5
                and re.search(r"[A-Za-z0-9]$", prev_text)
                and re.match(r"^[A-Za-z0-9]", text)
            ):
                output += " "
        output += text
        previous = item
    return re.sub(r"[ \t]+", " ", output).strip()


def restore_reading_order(items: list[dict[str, Any]]) -> str:
    """用 PDF 文字坐标恢复阅读顺序，并清理同坐标重复渲染文字。"""
    unique: list[dict[str, Any]] = []
    # 部分 PDF 为了加粗或描边，会把同一段文字以约 0.5px 的偏移渲染两次。
    # 不能只按四舍五入后的坐标去重，否则边界附近的副本仍会保留下来。
    seen_by_text: dict[str, list[tuple[float, float]]] = {}
    for item in items:
        text = str(item.get("text") or "").replace("\x00", "").strip()
        if not text:
            continue
        x = float(item.get("x") or 0)
        y = float(item.get("y") or 0)
        coordinates = seen_by_text.setdefault(text, [])
        if any(
            abs(old_x - x) <= 1.0 and abs(old_y - y) <= 1.0
            for old_x, old_y in coordinates
        ):
            continue
        coordinates.append((x, y))
        unique.append({**item, "text": text})

    # PDF 原点在左下：y 由大到小。两个文字坐标相差 2.5pt 以内视为同行。
    unique.sort(key=lambda row: (-float(row.get("y") or 0), float(row.get("x") or 0)))
    lines: list[list[dict[str, Any]]] = []
    line_y: list[float] = []
    for item in unique:
        y = float(item.get("y") or 0)
        target = next((i for i, value in enumerate(line_y) if abs(value - y) <= 2.5), None)
        if target is None:
            lines.append([item])
            line_y.append(y)
        else:
            lines[target].append(item)
    text_lines = [_join_pdf_line(line) for line in lines]
    return "\n".join(line for line in text_lines if line).strip()


async def _launch_public_browser(playwright, *, headless: bool = True):
    last_error: Exception | None = None
    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return await playwright.chromium.launch(channel=channel, headless=headless)
            return await playwright.chromium.launch(headless=headless)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"无法启动本地浏览器: {last_error}")


async def _extract_rendered_text_pages(
    frame, *, timeout_ms: int, max_pages: int = 100
) -> tuple[list[dict[str, Any]], int]:
    """逐页滚动 PDF.js，并从已经渲染的文字层读取正文。

    新版 PDF.js 不再保证 ``window.PDFViewerApplication`` 暴露为全局变量，
    但阅读器正常显示时，每页仍会生成 ``.textLayer``。逐页滚动可以触发
    懒加载，同时避免再次直连受访问策略保护的 PDF 地址。
    """
    page_locators = frame.locator(".page")
    try:
        await page_locators.first.wait_for(state="attached", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return [], 0

    page_count = await page_locators.count()
    pages: list[dict[str, Any]] = []
    for index in range(min(page_count, max_pages)):
        page = page_locators.nth(index)
        try:
            await page.scroll_into_view_if_needed(timeout=min(timeout_ms, 15_000))
            layer = page.locator(".textLayer")
            await layer.wait_for(state="attached", timeout=min(timeout_ms, 15_000))
            items = await layer.evaluate(
                """layer => {
                    const layerRect = layer.getBoundingClientRect();
                    return Array.from(layer.querySelectorAll('span')).map((span) => {
                        const rect = span.getBoundingClientRect();
                        return {
                            text: span.textContent || '',
                            x: rect.left - layerRect.left,
                            y: layerRect.bottom - rect.bottom,
                            width: rect.width,
                            height: rect.height,
                        };
                    });
                }"""
            )
            text = restore_reading_order(items or [])
            if not text:
                text = (await layer.inner_text(timeout=5_000)).strip()
        except Exception as exc:  # noqa: BLE001
            logger.info("PDF text layer page %s unavailable: %s", index + 1, exc)
            continue
        if text:
            pages.append(
                {"page": index + 1, "text": text, "method": "pdf_text_layer"}
            )
    return pages, page_count


async def _ocr_rendered_pages(
    frame, *, page_numbers: set[int] | None = None
) -> list[dict[str, Any]]:
    """扫描 PDF 无文本层时的本地 OCR 降级；组件未安装时安静失败。"""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return []

    pages = frame.locator(".page")
    count = await pages.count()
    output: list[dict[str, Any]] = []
    for index in range(min(count, 100)):
        page_number = index + 1
        if page_numbers is not None and page_number not in page_numbers:
            continue
        locator = pages.nth(index)
        try:
            await locator.scroll_into_view_if_needed(timeout=10_000)
            image_bytes = await locator.screenshot(timeout=20_000)
            image = Image.open(BytesIO(image_bytes))
            text = pytesseract.image_to_string(image, lang="chi_sim+eng").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info("PDF OCR page %s unavailable: %s", index + 1, exc)
            continue
        if text:
            output.append({"page": page_number, "text": text, "method": "ocr"})
    return output


async def _wait_for_pdf_frame(page, *, timeout_ms: int):
    """等待 iframe 对应的 Playwright Frame 真正挂载，避免元素先于 Frame 出现。"""
    deadline = asyncio.get_running_loop().time() + min(timeout_ms, 20_000) / 1000
    while asyncio.get_running_loop().time() < deadline:
        frame = next(
            (
                candidate
                for candidate in page.frames
                if "web_pdf" in candidate.url or "pdfjs" in candidate.url
            ),
            None,
        )
        if frame is not None:
            return frame
        await asyncio.sleep(0.25)
    return None


async def _collect_public_pdf_detail(
    page,
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int = 55_000,
    headless: bool = True,
) -> PublicPdfDetail:
    parsed_detail_url = urlparse(detail_url)
    if (
        parsed_detail_url.scheme.lower() != "https"
        or (parsed_detail_url.hostname or "").lower() != "ctbpsp.com"
    ):
        return PublicPdfDetail(
            status="failed",
            detail_url=detail_url,
            message="详情地址不是受信任的官方公告页面，已拒绝访问",
            failure_reason="invalid_detail_origin",
        )

    try:
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            await page.wait_for_selector(
                'iframe[src*="web_pdf"], iframe[src*="pdfjs"]',
                timeout=timeout_ms if not headless else min(timeout_ms, 20_000),
            )
            pdf_loaded = True
        except Exception:  # noqa: BLE001
            pdf_loaded = False
        if not pdf_loaded:
            try:
                body = (await page.locator("body").inner_text(timeout=10_000)).strip()
            except Exception:  # noqa: BLE001
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message="专用浏览器已关闭，公告详情尚未完成采集",
                    failure_reason="browser_closed",
                )
            lower = body.lower()
            if not headless:
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message=f"{timeout_ms // 1000} 秒内未检测到公告 PDF，请完成官方验证",
                    failure_reason="verification_timeout",
                )
            if any(marker.lower() in lower for marker in _VERIFY_MARKERS):
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message="详情页要求人工完成安全验证",
                    failure_reason="verification_required",
                )
            if _normalise_identity(expected_title) not in _normalise_identity(body):
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message="站点安全策略未放行公告详情，需在专用浏览器完成验证",
                    failure_reason="verification_required",
                )
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                message="详情页未加载 PDF.js 公告正文",
                failure_reason="pdf_not_loaded",
            )

        main_text = await page.locator("body").inner_text(timeout=10_000)
        current_id = parse_qs(urlparse(page.url.split("#/")[-1]).query).get("uuid", [""])[0]
        if not current_id:
            match = re.search(r"(?:[?&])uuid=([^&]+)", page.url)
            current_id = unquote(match.group(1)) if match else ""
        title_ok = _normalise_identity(expected_title) in _normalise_identity(main_text)
        id_ok = bool(expected_id and current_id == expected_id)
        if not (id_ok and title_ok):
            return PublicPdfDetail(
                status="failed",
                detail_url=detail_url,
                message="详情页 ID 或标题与列表记录不一致，已拒绝使用",
                failure_reason="identity_mismatch",
            )

        pdf_frame = await _wait_for_pdf_frame(page, timeout_ms=timeout_ms)
        if pdf_frame is None:
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                message="PDF.js iframe 未就绪",
                failure_reason="pdf_not_ready",
            )
        pdf_query = parse_qs(urlparse(pdf_frame.url).query).get("file", [None])[0]
        pdf_url = unquote(pdf_query) if pdf_query else None

        try:
            # iframe 元素出现时 PDF.js 可能仍在初始化。过早读取会得到空数组，
            # 随后退到 DOM 文字层；该文字层会把日期数字、表格值和标签拆开。
            # 优先等待官方阅读器已经持有完整文档对象，再读取逐页文本对象。
            await pdf_frame.wait_for_function(
                "() => (window.PDFViewerApplication?.pdfDocument?.numPages || 0) > 0",
                timeout=min(timeout_ms, 20_000),
            )
            raw_pages = await pdf_frame.evaluate(
                """async () => {
                const doc = window.PDFViewerApplication?.pdfDocument;
                if (!doc) return [];
                const pages = [];
                for (let pageNo = 1; pageNo <= doc.numPages; pageNo += 1) {
                    const page = await doc.getPage(pageNo);
                    const content = await page.getTextContent();
                    pages.push({
                        page: pageNo,
                        items: content.items.map((item) => ({
                            text: item.str || '',
                            x: item.transform?.[4] || 0,
                            y: item.transform?.[5] || 0,
                            width: item.width || 0,
                            height: item.height || 0,
                        })),
                    });
                }
                return pages;
            }"""
            )
        except Exception:  # noqa: BLE001
            raw_pages = []
        pages = [
            {
                "page": int(row.get("page") or index + 1),
                "text": restore_reading_order(row.get("items") or []),
                "method": "pdf_text",
            }
            for index, row in enumerate(raw_pages or [])
        ]
        pages = [row for row in pages if row["text"]]
        page_count = len(raw_pages or [])
        if not pages:
            pages, page_count = await _extract_rendered_text_pages(
                pdf_frame, timeout_ms=timeout_ms
            )

        extracted_numbers = {int(row["page"]) for row in pages}
        expected_numbers = set(range(1, min(page_count, 100) + 1))
        missing_numbers = expected_numbers - extracted_numbers
        if not pages or missing_numbers:
            ocr_pages = await _ocr_rendered_pages(
                pdf_frame,
                page_numbers=missing_numbers if pages else None,
            )
            by_page = {int(row["page"]): row for row in pages}
            by_page.update({int(row["page"]): row for row in ocr_pages})
            pages = [by_page[number] for number in sorted(by_page)]
            extracted_numbers = set(by_page)
            missing_numbers = expected_numbers - extracted_numbers

        methods = {str(row.get("method") or "") for row in pages}
        if methods == {"ocr"}:
            content_format = "pdf_ocr"
        elif "ocr" in methods:
            content_format = "pdf_mixed"
        else:
            content_format = "pdf_text"
        if not pages:
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message="PDF 无可用文本层，且本地 OCR 不可用或未识别到文字",
                failure_reason="content_unavailable",
            )
        if missing_numbers:
            missing = "、".join(str(number) for number in sorted(missing_numbers))
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message=f"PDF 第 {missing} 页未能读取，已拒绝把不完整正文标记为完整详情",
                failure_reason="incomplete_pdf_pages",
            )
        full_text = "\n".join(row["text"] for row in pages)
        if not _pdf_content_matches_title(expected_title, full_text):
            return PublicPdfDetail(
                status="failed",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message="PDF 正文标题与列表记录不一致，已拒绝使用",
                failure_reason="pdf_title_mismatch",
            )
        clean = "\n".join(f"【第{row['page']}页】\n{row['text']}" for row in pages)
        return PublicPdfDetail(
            status="full",
            detail_url=detail_url,
            content_format=content_format,
            clean_content=clean,
            pages=pages,
            pdf_url=pdf_url,
            message=f"已验证并读取 {len(pages)} 页 PDF 正文",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("public PDF detail unavailable for %s: %s", expected_id, exc)
        reason = "browser_closed" if "TargetClosed" in type(exc).__name__ else "collector_error"
        return PublicPdfDetail(
            status="needs_human_verification" if not headless else "metadata_only",
            detail_url=detail_url,
            message=f"PDF 详情采集失败: {type(exc).__name__}",
            failure_reason=reason,
        )


async def _fetch_managed_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int,
    headless: bool,
) -> PublicPdfDetail:
    from app.browser.managed_public import (
        ManagedPublicBrowserError,
        get_managed_public_browser,
    )

    broker = get_managed_public_browser()
    last_result: PublicPdfDetail | None = None
    for browser_attempt in range(2):
        try:
            async with broker.acquire(interactive=not headless) as lease:
                for tab_attempt in range(2):
                    page = await lease.context.new_page()
                    try:
                        result = await _collect_public_pdf_detail(
                            page,
                            detail_url=detail_url,
                            expected_id=expected_id,
                            expected_title=expected_title,
                            timeout_ms=timeout_ms,
                            headless=headless,
                        )
                    finally:
                        try:
                            await page.close()
                        except Exception:  # noqa: BLE001
                            pass
                    result.acquisition_mode = "managed_chrome"
                    result.browser_reused = lease.reused
                    # 返回发生在租约退出之前，此刻内部状态仍是 busy；对调用方
                    # 应报告本次操作完成后的可用状态，避免成功响应看起来仍在跑。
                    result.browser_state = (
                        "ready" if broker.is_connected else "unavailable"
                    )
                    last_result = result
                    if result.status == "full":
                        return result
                    if result.status == "needs_human_verification":
                        broker.mark_needs_verification()
                        result.browser_state = "needs_verification"
                        return result
                    if result.failure_reason in {
                        "identity_mismatch",
                        "pdf_title_mismatch",
                        "incomplete_pdf_pages",
                    }:
                        return result
                    if tab_attempt == 0:
                        await asyncio.sleep(1)
                if broker.is_connected:
                    return last_result
        except ManagedPublicBrowserError as exc:
            return PublicPdfDetail(
                status="needs_human_verification" if not headless else "metadata_only",
                detail_url=detail_url,
                message=f"专用浏览器不可用：{exc}",
                failure_reason="managed_browser_unavailable",
                acquisition_mode="managed_chrome",
                browser_state="unavailable",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "managed public browser crashed while collecting %s: %s",
                expected_id,
                type(exc).__name__,
            )
            last_result = PublicPdfDetail(
                status="needs_human_verification" if not headless else "metadata_only",
                detail_url=detail_url,
                message=f"专用浏览器意外退出: {type(exc).__name__}",
                failure_reason="browser_closed",
                acquisition_mode="managed_chrome",
                browser_state="unavailable",
            )
            await broker.shutdown()
            if browser_attempt == 0:
                continue
            return last_result
        if browser_attempt == 0:
            await broker.shutdown()
        elif not broker.is_connected:
            await broker.shutdown()
    return last_result or PublicPdfDetail(
        status="metadata_only",
        detail_url=detail_url,
        message="专用浏览器未返回公告详情",
        failure_reason="managed_browser_unavailable",
        acquisition_mode="managed_chrome",
        browser_state=broker.status()["state"],
    )


async def _fetch_legacy_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int,
    headless: bool,
) -> PublicPdfDetail:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return PublicPdfDetail(
            status="metadata_only",
            detail_url=detail_url,
            message="Playwright 未安装，无法读取 PDF.js 详情",
            failure_reason="playwright_missing",
        )
    async with async_playwright() as playwright:
        browser = None
        try:
            browser = await _launch_public_browser(playwright, headless=headless)
            context = await browser.new_context(
                locale="zh-CN", viewport={"width": 1440, "height": 1000}
            )
            page = await context.new_page()
            try:
                return await _collect_public_pdf_detail(
                    page,
                    detail_url=detail_url,
                    expected_id=expected_id,
                    expected_title=expected_title,
                    timeout_ms=timeout_ms,
                    headless=headless,
                )
            finally:
                await page.close()
        finally:
            if browser is not None:
                await browser.close()


async def fetch_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int = 55_000,
    headless: bool = True,
    managed: bool = False,
) -> PublicPdfDetail:
    if managed:
        return await _fetch_managed_public_pdf_detail(
            detail_url=detail_url,
            expected_id=expected_id,
            expected_title=expected_title,
            timeout_ms=timeout_ms,
            headless=headless,
        )
    return await _fetch_legacy_public_pdf_detail(
        detail_url=detail_url,
        expected_id=expected_id,
        expected_title=expected_title,
        timeout_ms=timeout_ms,
        headless=headless,
    )
