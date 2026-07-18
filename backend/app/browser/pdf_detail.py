"""公开 PDF.js 公告详情采集。

这里只读取浏览器正常渲染后的公开文本，不填验证码、不注入 Cookie，
也不尝试绕过站点的人机验证。遇到验证页时返回可审计状态，
由上层继续执行其他数据源。
"""

from __future__ import annotations

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


def _normalise_identity(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


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
    seen: set[tuple[str, int, int]] = set()
    for item in items:
        text = str(item.get("text") or "").replace("\x00", "").strip()
        if not text:
            continue
        key = (
            text,
            round(float(item.get("x") or 0) * 2),
            round(float(item.get("y") or 0) * 2),
        )
        if key in seen:
            continue
        seen.add(key)
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


async def _ocr_rendered_pages(frame) -> list[dict[str, Any]]:
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
            output.append({"page": index + 1, "text": text, "method": "ocr"})
    return output


async def fetch_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int = 55_000,
    headless: bool = True,
) -> PublicPdfDetail:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return PublicPdfDetail(
            status="metadata_only",
            detail_url=detail_url,
            message="Playwright 未安装，无法读取 PDF.js 详情",
        )

    async with async_playwright() as playwright:
        browser = None
        try:
            browser = await _launch_public_browser(playwright, headless=headless)
            context = await browser.new_context(
                locale="zh-CN",
                viewport={"width": 1440, "height": 1000},
            )
            page = await context.new_page()
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)
            pdf_loaded = False
            for attempt in range(2):
                try:
                    await page.wait_for_selector(
                        'iframe[src*="web_pdf"], iframe[src*="pdfjs"]',
                        timeout=max(20_000, timeout_ms // 2),
                    )
                    pdf_loaded = True
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 0:
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                        except Exception:  # noqa: BLE001
                            pass
            if not pdf_loaded:
                body = (await page.locator("body").inner_text(timeout=10_000)).strip()
                lower = body.lower()
                if any(marker.lower() in lower for marker in _VERIFY_MARKERS):
                    return PublicPdfDetail(
                        status="needs_human_verification",
                        detail_url=detail_url,
                        message="详情页要求人工完成安全验证",
                    )
                # 站点安全脚本未能放行详情 API 时，通常只剩入口导航与
                # about:blank iframe。这不等于「原文无详情」，必须标记待人工验证。
                if _normalise_identity(expected_title) not in _normalise_identity(body):
                    return PublicPdfDetail(
                        status="needs_human_verification",
                        detail_url=detail_url,
                        message="站点安全策略未放行公告详情，需人工在官方页面验证",
                    )
                return PublicPdfDetail(
                    status="metadata_only",
                    detail_url=detail_url,
                    message="详情页未加载 PDF.js 公告正文",
                )

            main_text = await page.locator("body").inner_text(timeout=10_000)
            current_id = parse_qs(urlparse(page.url.split("#/")[-1]).query).get("uuid", [""])[0]
            # hash 路由无法被 urlparse 直接视为 query，再用正则双重校验。
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
                )

            pdf_frame = next(
                (
                    frame
                    for frame in page.frames
                    if "web_pdf" in frame.url or "pdfjs" in frame.url
                ),
                None,
            )
            if pdf_frame is None:
                return PublicPdfDetail(
                    status="metadata_only", detail_url=detail_url, message="PDF.js iframe 未就绪"
                )
            try:
                await pdf_frame.wait_for_selector(".textLayer", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                # 扫描件可能不会生成文本层，下方继续走 OCR。
                pass
            await pdf_frame.wait_for_function(
                "window.PDFViewerApplication && "
                "window.PDFViewerApplication.pdfDocument",
                timeout=timeout_ms,
            )
            raw_pages = await pdf_frame.evaluate(
                """async () => {
                    const doc = window.PDFViewerApplication.pdfDocument;
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
            pages = [
                {
                    "page": int(row.get("page") or index + 1),
                    "text": restore_reading_order(row.get("items") or []),
                    "method": "pdf_text",
                }
                for index, row in enumerate(raw_pages or [])
            ]
            pages = [row for row in pages if row["text"]]
            content_format = "pdf_text"
            if not pages:
                pages = await _ocr_rendered_pages(pdf_frame)
                content_format = "pdf_ocr" if pages else None
            pdf_query = parse_qs(urlparse(pdf_frame.url).query).get("file", [None])[0]
            pdf_url = unquote(pdf_query) if pdf_query else None
            if not pages:
                return PublicPdfDetail(
                    status="metadata_only",
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    message="PDF 无可用文本层，且本地 OCR 不可用或未识别到文字",
                )
            full_text = "\n".join(row["text"] for row in pages)
            if _normalise_identity(expected_title) not in _normalise_identity(full_text):
                return PublicPdfDetail(
                    status="failed",
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    message="PDF 正文标题与列表记录不一致，已拒绝使用",
                )
            clean = "\n".join(
                f"【第{row['page']}页】\n{row['text']}" for row in pages
            )
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
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                message=f"PDF 详情采集失败: {type(exc).__name__}",
            )
        finally:
            if browser is not None:
                await browser.close()
