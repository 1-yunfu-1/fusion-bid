"""公开 PDF.js 公告详情采集。

这里只读取浏览器正常渲染后的公开文本，不填验证码、不注入 Cookie，
也不尝试绕过站点的人机验证。遇到验证页时返回可审计状态，
由上层继续执行其他数据源。
"""

from __future__ import annotations

import asyncio
import base64
from difflib import SequenceMatcher
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

_RAPID_OCR: Any | None = None
_RAPID_OCR_LOCK = threading.Lock()


def _recognise_image_bytes(image_bytes: bytes) -> str:
    """Run the bundled Chinese OCR model with one process-wide reusable session."""
    global _RAPID_OCR  # noqa: PLW0603
    from rapidocr import RapidOCR

    with _RAPID_OCR_LOCK:
        if _RAPID_OCR is None:
            _RAPID_OCR = RapidOCR()
        result = _RAPID_OCR(image_bytes)
    texts = tuple(getattr(result, "txts", None) or ())
    return "\n".join(str(value).strip() for value in texts if str(value).strip())

_VERIFY_MARKERS = (
    "安全验证",
    "人机验证",
    "请完成验证",
    "验证码",
    "拖动滑块",
    "访问过于频繁",
    "captcha",
)

_FAILURE_STAGES = {
    "invalid_detail_origin": "navigation",
    "browser_closed": "navigation",
    "managed_browser_unavailable": "navigation",
    "collector_error": "navigation",
    "site_rate_limited": "navigation",
    "verification_required": "outer_page",
    "verification_timeout": "outer_page",
    "pdf_not_loaded": "pdf_frame",
    "identity_mismatch": "outer_identity",
    "pdf_not_ready": "pdf_frame",
    "content_unavailable": "pdf_pages",
    "incomplete_pdf_pages": "pdf_pages",
    "collector_timeout": "pdf_pages",
    "pdf_title_mismatch": "pdf_identity",
}


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
    failure_stage: str | None = None
    attempt_count: int = 0
    duration_ms: int = 0
    validation_signals: dict[str, Any] = field(default_factory=dict)
    site_blocked: bool = False


def _normalise_identity(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


_DOCUMENT_TITLE_SUFFIXES = (
    "国际招标澄清或变更公告",
    "国际招标公告",
    "招标澄清或变更公告",
    "澄清或变更公告",
    "中标候选人公示",
    "资格预审公告",
    "竞争性谈判公告",
    "竞争性磋商公告",
    "询比采购公告",
    "采购邀请公告",
    "公开招标公告",
    "项目公告",
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

_TITLE_DECORATIONS = (
    "电子标",
    "重新招标",
    "重新采购",
)


def _strip_document_wrapper(value: str) -> str:
    """移除公告类型和展示装饰，但保留项目主体。"""
    normalised = _normalise_identity(value)
    decoration = "(?:" + "|".join(
        re.escape(_normalise_identity(item)) for item in _TITLE_DECORATIONS
    ) + r"|第?\d+次|\d+)*"
    changed = True
    while changed and normalised:
        changed = False
        for suffix in sorted(_DOCUMENT_TITLE_SUFFIXES, key=len, reverse=True):
            suffix_value = _normalise_identity(suffix)
            updated = re.sub(
                rf"{re.escape(suffix_value)}{decoration}$", "", normalised
            )
            if updated != normalised:
                normalised = updated
                changed = True
                break
    for decoration_value in _TITLE_DECORATIONS:
        token = _normalise_identity(decoration_value)
        normalised = re.sub(rf"{re.escape(token)}$", "", normalised)
    return normalised


def _normalise_project_code(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", value or "").upper()


def _codes_compatible(expected: str | None, observed: str | None) -> bool:
    left = _normalise_project_code(expected)
    right = _normalise_project_code(observed)
    if not left or not right:
        return False
    if left == right:
        return True
    # CEBPUB 列表偶尔给国际招标编号追加平台用的 000；只接受这一种保守差异。
    return (
        len(left) >= 11
        and len(right) >= 8
        and (left == f"{right}000" or right == f"{left}000")
    )


def _identity_candidates(full_text: str) -> tuple[list[str], list[str], list[str]]:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in full_text.splitlines()]
    lines = [line for line in lines if line]
    projects: list[str] = []
    codes: list[str] = []
    subjects: list[str] = []
    project_patterns = (
        r"(?:本招标项目名称为|招标项目名称|项目名称|本招标项目|本采购项目|本项目)\s*[：:]?\s*(.{4,180}?)(?=（?\s*(?:项目|招标|采购)编号|已批准|已具备|[，。])",
        r"^(.{4,160}?项目)\s*已批准",
    )
    code_pattern = re.compile(
        r"(?:项目|招标|采购)编号\s*[：:]?\s*([A-Za-z0-9][A-Za-z0-9._/\-]{5,80})",
        re.I,
    )
    subject_pattern = re.compile(
        r"(?:招标人|采购人)\s*(?:为|[：:])\s*([^，。；\n]{4,100})"
    )
    for line in lines[:120]:
        for pattern in project_patterns:
            match = re.search(pattern, line)
            if match:
                value = match.group(1).strip(" ：:，。()（）")
                if value and value not in projects:
                    projects.append(value)
        for match in code_pattern.finditer(line):
            value = match.group(1).strip()
            if value not in codes:
                codes.append(value)
        for match in subject_pattern.finditer(line):
            value = match.group(1).strip(" ：:，。()（）")
            if value and value not in subjects:
                subjects.append(value)
    return projects[:5], codes[:5], subjects[:5]


def _pdf_identity_signals(
    expected_title: str,
    full_text: str,
    *,
    expected_project_code: str | None = None,
) -> dict[str, Any]:
    expected = _normalise_identity(expected_title)
    core = _strip_document_wrapper(expected_title)
    actual = _normalise_identity(full_text)
    projects, codes, subjects = _identity_candidates(full_text)
    normalised_projects = [_normalise_identity(value) for value in projects]
    normalised_subjects = [_normalise_identity(value) for value in subjects]

    exact_title = bool(expected and expected in actual)
    core_in_document = bool(core and len(core) >= 8 and core in actual)
    project_name_match = any(
        len(value) >= 8 and (value in expected or (core and core in value))
        for value in normalised_projects
    )
    company_match = any(
        len(value) >= 6 and value in expected for value in normalised_subjects
    )
    longest_common = (
        SequenceMatcher(None, core, actual, autojunk=False).find_longest_match().size
        if core and actual
        else 0
    )
    subject_overlap = longest_common >= 8
    matched_code = next(
        (value for value in codes if _codes_compatible(expected_project_code, value)),
        None,
    )
    project_code_match = matched_code is not None
    accepted = bool(
        exact_title
        or core_in_document
        or project_name_match
        or (company_match and subject_overlap)
        or (project_code_match and (company_match or longest_common >= 6))
    )
    if exact_title:
        method = "exact_title"
    elif core_in_document:
        method = "title_core"
    elif project_name_match:
        method = "project_name"
    elif company_match and subject_overlap:
        method = "subject+project_overlap"
    elif project_code_match and (company_match or longest_common >= 6):
        method = "project_code+independent_signal"
    else:
        method = "unverified"
    return {
        "accepted": accepted,
        "method": method,
        "exact_title": exact_title,
        "title_core_match": core_in_document,
        "project_name_match": project_name_match,
        "project_code_match": project_code_match,
        "company_match": company_match,
        "longest_common_chars": longest_common,
        "observed_project_code": matched_code,
    }


def _pdf_content_matches_title(expected_title: str, full_text: str) -> bool:
    """校验 PDF 正文与外层标题的项目主体一致。

    部分官方 PDF 正文从“招标条件”开始，正文只写项目名称，不重复外层
    “招标公告/澄清或变更公告(序号)”后缀。此时仍要求较长的项目主体完整
    出现在 PDF 中；不能因公告类型固定后缀缺失而误判，也不能退化为关键词
    模糊匹配。
    """
    return bool(_pdf_identity_signals(expected_title, full_text)["accepted"])


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
    """扫描 PDF 无文本层时使用随应用安装的本地 OCR 引擎。

    RapidOCR 自带中文模型并通过 ONNX Runtime 执行，不依赖用户额外安装
    Tesseract。模型实例在进程内复用；OCR 放到工作线程，避免阻塞浏览器队列。
    """
    try:
        from rapidocr import RapidOCR  # noqa: F401
    except ImportError:
        logger.warning("RapidOCR is unavailable; scanned PDF pages cannot be read")
        return []

    pages = frame.locator(".page")
    try:
        document_page_count = int(
            await frame.evaluate(
                "() => window.PDFViewerApplication?.pdfDocument?.numPages || 0"
            )
        )
    except Exception:  # noqa: BLE001
        document_page_count = 0
    count = document_page_count or await pages.count()
    output: list[dict[str, Any]] = []
    for index in range(min(count, 100)):
        page_number = index + 1
        if page_numbers is not None and page_number not in page_numbers:
            continue
        try:
            encoded = await frame.evaluate(
                """async (pageNo) => {
                    const doc = window.PDFViewerApplication?.pdfDocument;
                    if (!doc) return '';
                    const render = async () => {
                        const pdfPage = await doc.getPage(pageNo);
                        const base = pdfPage.getViewport({ scale: 1 });
                        const scale = Math.min(2.2, Math.sqrt(2200000 / Math.max(1, base.width * base.height)));
                        const viewport = pdfPage.getViewport({ scale });
                        const canvas = document.createElement('canvas');
                        canvas.width = Math.ceil(viewport.width);
                        canvas.height = Math.ceil(viewport.height);
                        const context = canvas.getContext('2d', { alpha: false });
                        context.fillStyle = '#ffffff';
                        context.fillRect(0, 0, canvas.width, canvas.height);
                        await pdfPage.render({
                            canvasContext: context,
                            viewport,
                            background: '#ffffff',
                        }).promise;
                        return canvas.toDataURL('image/png').split(',', 2)[1] || '';
                    };
                    return await Promise.race([
                        render(),
                        new Promise((resolve) => setTimeout(() => resolve(''), 12000)),
                    ]);
                }""",
                page_number,
            )
            image_bytes = base64.b64decode(encoded) if encoded else b""
            if not image_bytes:
                raise RuntimeError("PDF.js did not render the requested page")
            text = (
                await asyncio.wait_for(
                    asyncio.to_thread(_recognise_image_bytes, image_bytes),
                    timeout=45,
                )
            ).strip()
        except Exception as exc:  # noqa: BLE001
            # Older viewer builds may not expose the document object.  The DOM
            # screenshot remains a conservative fallback for those builds.
            try:
                locator = pages.nth(index)
                await locator.scroll_into_view_if_needed(timeout=10_000)
                image_bytes = await locator.screenshot(timeout=20_000)
                text = (
                    await asyncio.wait_for(
                        asyncio.to_thread(_recognise_image_bytes, image_bytes),
                        timeout=45,
                    )
                ).strip()
            except Exception as fallback_exc:  # noqa: BLE001
                logger.info(
                    "PDF OCR page %s unavailable: %s; fallback: %s",
                    page_number,
                    exc,
                    fallback_exc,
                )
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
    expected_project_code: str | None = None,
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
            failure_stage="navigation",
        )

    try:
        # ctbpsp 是 Hash 路由 SPA。直接从一个 UUID goto 另一个 UUID 时，外层标题
        # 可能先更新而旧 PDF iframe 仍留在页面中；先离开站点才能建立可靠导航边界。
        await page.goto(
            "about:blank", wait_until="commit", timeout=min(timeout_ms, 10_000)
        )
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
                    failure_stage="navigation",
                )
            lower = body.lower()
            if not headless:
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message=f"{timeout_ms // 1000} 秒内未检测到公告 PDF，请完成官方验证",
                    failure_reason="verification_timeout",
                    failure_stage="outer_page",
                )
            if any(marker.lower() in lower for marker in _VERIFY_MARKERS):
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message="详情页要求人工完成安全验证",
                    failure_reason="verification_required",
                    failure_stage="outer_page",
                )
            if _normalise_identity(expected_title) not in _normalise_identity(body):
                return PublicPdfDetail(
                    status="needs_human_verification",
                    detail_url=detail_url,
                    message="站点安全策略未放行公告详情，需在专用浏览器完成验证",
                    failure_reason="verification_required",
                    failure_stage="outer_page",
                )
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                message="详情页未加载 PDF.js 公告正文",
                failure_reason="pdf_not_loaded",
                failure_stage="pdf_frame",
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
                failure_stage="outer_identity",
                validation_signals={
                    "uuid_match": id_ok,
                    "outer_title_match": title_ok,
                },
            )

        pdf_frame = await _wait_for_pdf_frame(page, timeout_ms=timeout_ms)
        if pdf_frame is None:
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                message="PDF.js iframe 未就绪",
                failure_reason="pdf_not_ready",
                failure_stage="pdf_frame",
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
            document_page_count = int(
                await pdf_frame.evaluate(
                    "() => window.PDFViewerApplication?.pdfDocument?.numPages || 0"
                )
            )
            raw_pages = await pdf_frame.evaluate(
                """async () => {
                const doc = window.PDFViewerApplication?.pdfDocument;
                if (!doc) return [];
                const extract = async () => {
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
                };
                return await Promise.race([
                    extract(),
                    new Promise((resolve) => setTimeout(() => resolve([]), 12000)),
                ]);
            }"""
            )
        except Exception:  # noqa: BLE001
            raw_pages = []
            document_page_count = 0
        pages = [
            {
                "page": int(row.get("page") or index + 1),
                "text": restore_reading_order(row.get("items") or []),
                "method": "pdf_text",
            }
            for index, row in enumerate(raw_pages or [])
        ]
        pages = [row for row in pages if row["text"]]
        page_count = document_page_count or len(raw_pages or [])
        if not pages and not page_count:
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
                failure_stage="pdf_pages",
            )
        if missing_numbers:
            missing = "、".join(str(number) for number in sorted(missing_numbers))
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message=f"PDF 第 {missing} 页未能读取，已拒绝把不完整正文标记为完整详情",
                failure_reason="incomplete_pdf_pages",
                failure_stage="pdf_pages",
            )
        full_text = "\n".join(row["text"] for row in pages)
        validation_signals = _pdf_identity_signals(
            expected_title,
            full_text,
            expected_project_code=expected_project_code,
        )
        validation_signals.update(
            {
                "uuid_match": id_ok,
                "outer_title_match": title_ok,
                "complete_pages": not missing_numbers,
            }
        )
        if not validation_signals["accepted"]:
            return PublicPdfDetail(
                status="failed",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message="PDF 正文标题与列表记录不一致，已拒绝使用",
                failure_reason="pdf_title_mismatch",
                failure_stage="pdf_identity",
                validation_signals=validation_signals,
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
            validation_signals=validation_signals,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("public PDF detail unavailable for %s: %s", expected_id, exc)
        reason = "browser_closed" if "TargetClosed" in type(exc).__name__ else "collector_error"
        return PublicPdfDetail(
            status="needs_human_verification" if not headless else "metadata_only",
            detail_url=detail_url,
            message=f"PDF 详情采集失败: {type(exc).__name__}",
            failure_reason=reason,
            failure_stage=_FAILURE_STAGES.get(reason, "navigation"),
        )


async def _fetch_managed_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int,
    headless: bool,
    expected_project_code: str | None = None,
) -> PublicPdfDetail:
    from app.browser.managed_public import (
        ManagedPublicBrowserError,
        get_managed_public_browser,
    )

    broker = get_managed_public_browser()
    last_result: PublicPdfDetail | None = None
    started_at = time.monotonic()
    attempt_count = 0
    for browser_attempt in range(2):
        try:
            async with broker.acquire(interactive=not headless) as lease:
                page = lease.page
                blocked_statuses: set[int] = set()

                def remember_blocked_response(response) -> None:
                    try:
                        host = (urlparse(response.url).hostname or "").lower()
                        if host.endswith("ctbpsp.com") and response.status in {403, 429}:
                            blocked_statuses.add(int(response.status))
                    except Exception:  # noqa: BLE001
                        return

                listener_supported = hasattr(page, "on") and hasattr(
                    page, "remove_listener"
                )
                for page_attempt in range(2):
                    if listener_supported:
                        page.on("response", remember_blocked_response)
                    attempt_count += 1
                    try:
                        try:
                            result = await asyncio.wait_for(
                                _collect_public_pdf_detail(
                                    page,
                                    detail_url=detail_url,
                                    expected_id=expected_id,
                                    expected_title=expected_title,
                                    expected_project_code=expected_project_code,
                                    timeout_ms=timeout_ms,
                                    headless=headless,
                                ),
                                timeout=max(15, timeout_ms / 1000 + 5),
                            )
                        except TimeoutError:
                            result = PublicPdfDetail(
                                status="metadata_only",
                                detail_url=detail_url,
                                message=(
                                    "PDF 内容流在采集时限内未完成，已释放工作页，"
                                    "后续公告继续采集"
                                ),
                                failure_reason="collector_timeout",
                                failure_stage="pdf_pages",
                            )
                    finally:
                        if listener_supported:
                            try:
                                page.remove_listener("response", remember_blocked_response)
                            except Exception:  # noqa: BLE001
                                pass
                    result.attempt_count = attempt_count
                    result.duration_ms = int((time.monotonic() - started_at) * 1000)
                    result.acquisition_mode = "managed_chrome"
                    result.browser_reused = lease.reused
                    if blocked_statuses and result.status != "full":
                        result.site_blocked = True
                        result.failure_reason = "site_rate_limited"
                        result.failure_stage = "navigation"
                        result.message = (
                            f"官方站点返回 {min(blocked_statuses)}，本次详情未继续读取"
                        )
                    # 返回发生在租约退出之前，此刻内部状态仍是 busy；对调用方
                    # 应报告本次操作完成后的可用状态，避免成功响应看起来仍在跑。
                    result.browser_state = (
                        "ready" if broker.is_connected else "unavailable"
                    )
                    last_result = result
                    if result.failure_reason == "browser_closed":
                        raise RuntimeError("managed public browser page closed")
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
                    } and page_attempt == 0:
                        page = await broker.replace_page(lease)
                        continue
                    if result.failure_reason in {
                        "identity_mismatch",
                        "pdf_title_mismatch",
                        "incomplete_pdf_pages",
                        "site_rate_limited",
                    }:
                        return result
                    return result
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
                failure_stage="navigation",
                attempt_count=attempt_count,
                duration_ms=int((time.monotonic() - started_at) * 1000),
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
                failure_stage="navigation",
                attempt_count=attempt_count,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            if browser_attempt == 0:
                continue
            return last_result
    return last_result or PublicPdfDetail(
        status="metadata_only",
        detail_url=detail_url,
        message="专用浏览器未返回公告详情",
        failure_reason="managed_browser_unavailable",
        acquisition_mode="managed_chrome",
        browser_state=broker.status()["state"],
        failure_stage="navigation",
        attempt_count=attempt_count,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )


async def _fetch_legacy_public_pdf_detail(
    *,
    detail_url: str,
    expected_id: str,
    expected_title: str,
    timeout_ms: int,
    headless: bool,
    expected_project_code: str | None = None,
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
                    expected_project_code=expected_project_code,
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
    expected_project_code: str | None = None,
    timeout_ms: int = 55_000,
    headless: bool = True,
    managed: bool = False,
) -> PublicPdfDetail:
    if managed:
        return await _fetch_managed_public_pdf_detail(
            detail_url=detail_url,
            expected_id=expected_id,
            expected_title=expected_title,
            expected_project_code=expected_project_code,
            timeout_ms=timeout_ms,
            headless=headless,
        )
    return await _fetch_legacy_public_pdf_detail(
        detail_url=detail_url,
        expected_id=expected_id,
        expected_title=expected_title,
        expected_project_code=expected_project_code,
        timeout_ms=timeout_ms,
        headless=headless,
    )
