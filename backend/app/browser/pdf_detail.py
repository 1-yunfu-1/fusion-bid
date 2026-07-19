"""公开 PDF.js 公告详情采集。

这里只读取浏览器正常渲染后的公开文本，不填验证码、不注入 Cookie，
也不尝试绕过站点的人机验证。遇到验证页时返回可审计状态，
由上层继续执行其他数据源。
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
import importlib.util
import io
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, unquote, urlparse
from weakref import WeakKeyDictionary

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RAPID_OCR: Any | None = None
_RAPID_OCR_LOCK = threading.Lock()
_PDF_PARSE_SEMAPHORES: WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = WeakKeyDictionary()


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


def pdf_pipeline_status() -> dict[str, Any]:
    """Return dependency readiness without importing models or exposing paths."""
    text_parser = importlib.util.find_spec("pypdf") is not None
    rasterizer = importlib.util.find_spec("fitz") is not None
    ocr_engine = (
        importlib.util.find_spec("rapidocr") is not None
        and importlib.util.find_spec("onnxruntime") is not None
    )
    settings = get_settings()
    return {
        "memory_pdf_bytes": True,
        "text_parser": text_parser,
        "rasterizer": rasterizer,
        "ocr_engine": ocr_engine,
        "text_ready": text_parser or rasterizer,
        "scanned_pdf_ready": rasterizer and ocr_engine,
        "parse_concurrency": settings.cebpub_pdf_parse_concurrency,
        "ocr_concurrency": 1,
        "viewer_ready_timeout_seconds": settings.cebpub_pdf_ready_timeout_seconds,
        "ocr_timeout_seconds": settings.cebpub_ocr_timeout_seconds,
        "invalid_pdf_cooldown_hours": settings.cebpub_invalid_pdf_cooldown_hours,
    }


@asynccontextmanager
async def _pdf_parse_slot() -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    semaphore = _PDF_PARSE_SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(get_settings().cebpub_pdf_parse_concurrency)
        _PDF_PARSE_SEMAPHORES[loop] = semaphore
    async with semaphore:
        yield

_VERIFY_MARKERS = (
    "安全验证",
    "人机验证",
    "请完成验证",
    "验证码",
    "拖动滑块",
    "访问过于频繁",
    "captcha",
)

_INVALID_PDF_MARKERS = (
    "无效或损坏的 pdf",
    "无效或损坏的pdf",
    "pdf 文件已损坏",
    "pdf文件已损坏",
    "文件已损坏，无法修复",
    "invalid or corrupted pdf",
    "invalid pdf structure",
    "format error: not a pdf",
    "bad xref",
    "damaged pdf",
    "corrupt pdf",
)

_MISSING_PDF_MARKERS = (
    "missing pdf",
    "pdf file not found",
    "找不到 pdf",
    "找不到pdf",
    "文件不存在",
    "404 not found",
    "410 gone",
)

_TERMINAL_FAILURE_REASONS = {
    "pdf_invalid_or_corrupt",
    "pdf_too_large",
    "pdf_page_limit",
    "pdf_parse_failure",
    "ocr_failure",
    "ocr_timeout",
    "official_content_unavailable",
}

_RETRYABLE_FAILURE_REASONS = {
    "outer_detail_unavailable",
    "pdf_not_loaded",
    "pdf_not_ready",
    "pdf_document_unavailable",
    "pdf_bytes_timeout",
    "collector_timeout",
    # SPA 残留可能造成一次身份错配，强制新建标签页后允许重试一次。
    "identity_mismatch",
    "pdf_title_mismatch",
}

_FAILURE_STAGES = {
    "invalid_detail_origin": "navigation",
    "browser_closed": "navigation",
    "managed_browser_unavailable": "navigation",
    "collector_error": "navigation",
    "site_rate_limited": "navigation",
    "verification_required": "outer_page",
    "verification_timeout": "outer_page",
    "outer_detail_unavailable": "outer_page",
    "pdf_not_loaded": "pdf_frame",
    "identity_mismatch": "outer_identity",
    "pdf_not_ready": "pdf_frame",
    "pdf_document_unavailable": "pdf_frame",
    "pdf_bytes_timeout": "pdf_bytes",
    "pdf_too_large": "pdf_bytes",
    "pdf_invalid_or_corrupt": "pdf_validation",
    "pdf_page_limit": "pdf_parse",
    "pdf_parse_failure": "pdf_parse",
    "ocr_failure": "pdf_ocr",
    "ocr_timeout": "pdf_ocr",
    "official_content_unavailable": "pdf_identity",
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
    acquisition_path: str | None = None
    document_page_count: int = 0
    terminal_failure: bool = False
    retryable: bool = False
    viewer_error_code: str | None = None
    viewer_error_message: str | None = None
    fallback_attempted: bool = False
    fallback_result: str | None = None
    time_to_failure_ms: int = 0
    document_bytes: bytes | None = field(default=None, repr=False)


def _viewer_error_reason(message: str) -> str:
    """Classify only an explicit PDF.js viewer error, never generic console noise."""
    probe = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if any(marker in probe for marker in _INVALID_PDF_MARKERS):
        return "pdf_invalid_or_corrupt"
    if any(marker in probe for marker in _MISSING_PDF_MARKERS):
        return "official_content_unavailable"
    return "pdf_document_unavailable"


def _apply_failure_policy(detail: PublicPdfDetail) -> PublicPdfDetail:
    reason = str(detail.failure_reason or "")
    detail.terminal_failure = reason in _TERMINAL_FAILURE_REASONS
    detail.retryable = reason in _RETRYABLE_FAILURE_REASONS
    if reason and not detail.failure_stage:
        detail.failure_stage = _FAILURE_STAGES.get(reason, "detail_acquisition")
    if reason and detail.duration_ms:
        detail.time_to_failure_ms = detail.duration_ms
    return detail


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
    matcher = SequenceMatcher(None, core, actual, autojunk=False) if core and actual else None
    matching_blocks = matcher.get_matching_blocks() if matcher else []
    longest_common = max((block.size for block in matching_blocks), default=0)
    substantial_blocks = [block for block in matching_blocks if block.size >= 3]
    covered_chars = sum(block.size for block in substantial_blocks)
    span_coverage = covered_chars / len(core) if core else 0.0
    # 列表标题偶有一个连接字错误，或把两个物资名称连写；PDF 表格中又会分行。
    # 仅在两个以上有序片段覆盖标题主体至少 75% 时接受，避免退化成单关键词命中。
    title_span_match = bool(
        len(core) >= 8
        and len(substantial_blocks) >= 2
        and longest_common >= 4
        and span_coverage >= 0.75
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
        or title_span_match
        or (company_match and subject_overlap)
        or (project_code_match and (company_match or longest_common >= 6))
    )
    if exact_title:
        method = "exact_title"
    elif core_in_document:
        method = "title_core"
    elif project_name_match:
        method = "project_name"
    elif title_span_match:
        method = "ordered_title_spans"
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
        "title_span_match": title_span_match,
        "title_span_coverage": round(span_coverage, 3),
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
    output: list[str] = []
    for line in text_lines:
        if not line:
            continue
        # Overlay PDFs often draw the same row twice.  Coordinate-level
        # deduplication handles most cases; this removes only exact adjacent
        # line duplicates and therefore does not merge legitimate clauses.
        if output and line == output[-1]:
            continue
        output.append(line)
    return "\n".join(output).strip()


async def _read_loaded_pdf_bytes(
    frame, *, timeout_ms: int, max_bytes: int
) -> tuple[bytes | None, int, str | None]:
    """Read the PDF already held by PDF.js without requesting its URL again."""
    handle = None
    try:
        handle = await asyncio.wait_for(
            frame.evaluate_handle(
                """async () => {
                    const doc = window.PDFViewerApplication?.pdfDocument;
                    if (!doc) return null;
                    return await doc.getData();
                }"""
            ),
            timeout=max(5, min(timeout_ms, 15_000) / 1000),
        )
        length = int(
            await handle.evaluate(
                "data => data ? (data.byteLength || data.length || 0) : 0"
            )
        )
        if length <= 0:
            return None, 0, "pdf_document_unavailable"
        if length > max_bytes:
            return None, 0, "pdf_too_large"

        chunks: list[bytes] = []
        chunk_size = 256 * 1024
        for offset in range(0, length, chunk_size):
            encoded = await handle.evaluate(
                """(data, range) => {
                    const chunk = data.subarray(
                        range.offset,
                        Math.min(data.length, range.offset + range.size)
                    );
                    let binary = '';
                    for (let i = 0; i < chunk.length; i += 1) {
                        binary += String.fromCharCode(chunk[i]);
                    }
                    return btoa(binary);
                }""",
                {"offset": offset, "size": chunk_size},
            )
            chunks.append(base64.b64decode(encoded))
        data = b"".join(chunks)
        if len(data) != length:
            return None, 0, "pdf_bytes_timeout"
        if not data.startswith(b"%PDF-"):
            return None, 0, "pdf_invalid_or_corrupt"
        try:
            page_count = int(
                await frame.evaluate(
                    "() => window.PDFViewerApplication?.pdfDocument?.numPages || 0"
                )
            )
        except Exception:  # noqa: BLE001
            page_count = 0
        return data, page_count, None
    except TimeoutError:
        return None, 0, "pdf_bytes_timeout"
    except Exception as exc:  # noqa: BLE001
        logger.info("loaded PDF bytes unavailable: %s", type(exc).__name__)
        return None, 0, "pdf_document_unavailable"
    finally:
        if handle is not None:
            try:
                await handle.dispose()
            except Exception:  # noqa: BLE001
                pass


def _fitz_page_text(page: Any) -> str:
    payload = page.get_text("dict") or {}
    page_height = float(page.rect.height)
    items: list[dict[str, Any]] = []
    for block in payload.get("blocks") or []:
        if int(block.get("type") or 0) != 0:
            continue
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                text = str(span.get("text") or "").strip()
                bbox = span.get("bbox") or (0, 0, 0, 0)
                if not text or len(bbox) < 4:
                    continue
                x0, y0, x1, y1 = (float(value or 0) for value in bbox[:4])
                items.append(
                    {
                        "text": text,
                        "x": x0,
                        "y": page_height - y1,
                        "width": max(0.0, x1 - x0),
                        "height": max(0.0, y1 - y0),
                    }
                )
    return restore_reading_order(items)


def _parse_pdf_bytes_sync(
    data: bytes, *, max_pages: int
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Extract every page locally; OCR only pages without usable text."""
    fitz_error: Exception | None = None
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            page_count = len(document)
            if page_count > max_pages:
                return [], page_count, "pdf_page_limit"
            pages: list[dict[str, Any]] = []
            missing: list[int] = []
            for index, page in enumerate(document):
                text = _fitz_page_text(page).strip()
                method = "pymupdf_text"
                if len(_normalise_identity(text)) < 20:
                    try:
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        ocr_text = _recognise_image_bytes(pixmap.tobytes("png")).strip()
                    except Exception as exc:  # noqa: BLE001
                        logger.info("local PDF OCR page %s failed: %s", index + 1, exc)
                        ocr_text = ""
                    if len(_normalise_identity(ocr_text)) > len(
                        _normalise_identity(text)
                    ):
                        text = ocr_text
                        method = "ocr"
                if text:
                    pages.append(
                        {"page": index + 1, "text": text, "method": method}
                    )
                else:
                    missing.append(index + 1)
            if missing:
                reason = "ocr_failure" if len(missing) == page_count else "incomplete_pdf_pages"
                return pages, page_count, reason
            return pages, page_count, None
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001
        fitz_error = exc
        logger.info("PyMuPDF extraction unavailable: %s", type(exc).__name__)

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
        if page_count > max_pages:
            return [], page_count, "pdf_page_limit"
        pages = []
        missing = []
        for index, page in enumerate(reader.pages):
            try:
                text = (page.extract_text(extraction_mode="layout") or "").strip()
            except TypeError:
                text = (page.extract_text() or "").strip()
            if text:
                pages.append(
                    {"page": index + 1, "text": text, "method": "pypdf_text"}
                )
            else:
                missing.append(index + 1)
        if missing:
            return pages, page_count, "incomplete_pdf_pages"
        return pages, page_count, None
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "local PDF parsing failed: fitz=%s pypdf=%s",
            type(fitz_error).__name__ if fitz_error else "none",
            type(exc).__name__,
        )
        # 两个独立解析器都拒绝同一份已完整取得的字节，才把它判为损坏。
        return [], 0, "pdf_invalid_or_corrupt"


async def _finalise_captured_pdf(
    captured: PublicPdfDetail,
    *,
    expected_title: str,
    expected_project_code: str | None,
) -> PublicPdfDetail:
    data = captured.document_bytes
    captured.document_bytes = None
    if not data:
        captured.status = "metadata_only"
        captured.failure_reason = "pdf_document_unavailable"
        captured.failure_stage = "pdf_frame"
        captured.message = "PDF.js 未提供可解析的文档字节"
        return captured

    settings = get_settings()
    try:
        async with _pdf_parse_slot():
            pages, page_count, parse_reason = await asyncio.wait_for(
                asyncio.to_thread(
                    _parse_pdf_bytes_sync,
                    data,
                    max_pages=settings.cebpub_pdf_max_pages,
                ),
                timeout=settings.cebpub_ocr_timeout_seconds,
            )
    except TimeoutError:
        pages, page_count, parse_reason = [], captured.document_page_count, "ocr_timeout"
    captured.document_page_count = page_count or captured.document_page_count
    captured.acquisition_path = "pdfjs_memory_bytes+local_parser"
    if parse_reason:
        captured.status = "metadata_only"
        captured.failure_reason = parse_reason
        captured.failure_stage = _FAILURE_STAGES.get(parse_reason, "pdf_parse")
        if parse_reason == "pdf_page_limit":
            captured.message = (
                f"PDF 共 {captured.document_page_count} 页，超过本机安全上限，未标记为完整正文"
            )
        elif parse_reason == "ocr_failure":
            captured.message = "PDF 页面无可用文字，本地 OCR 未识别到正文"
        elif parse_reason == "ocr_timeout":
            captured.message = "PDF 本地文字识别超过安全时限，已停止等待"
        elif parse_reason == "incomplete_pdf_pages":
            captured.message = "PDF 存在未能读取的页面，未标记为完整正文"
        elif parse_reason == "pdf_invalid_or_corrupt":
            captured.message = "PDF 字节无效或已损坏，本地解析器无法打开"
        else:
            captured.message = "已取得 PDF，但本地解析失败"
        captured.pages = pages
        return captured

    full_text = "\n".join(str(row.get("text") or "") for row in pages)
    if any(
        marker in full_text
        for marker in ("页面访问提示", "当前页面已暂停访问", "不再提供PDF文件")
    ):
        captured.status = "metadata_only"
        captured.failure_reason = "official_content_unavailable"
        captured.failure_stage = "pdf_identity"
        captured.message = "官方查看器返回暂停访问提示，未将提示页当作公告正文"
        captured.pages = []
        return captured

    validation_signals = _pdf_identity_signals(
        expected_title,
        full_text,
        expected_project_code=expected_project_code,
    )
    validation_signals.update(captured.validation_signals)
    validation_signals["complete_pages"] = len(pages) == captured.document_page_count
    captured.validation_signals = validation_signals
    if not validation_signals["accepted"]:
        captured.status = "failed"
        captured.failure_reason = "pdf_title_mismatch"
        captured.failure_stage = "pdf_identity"
        captured.message = "PDF 正文身份与列表记录不一致，已拒绝使用"
        captured.pages = []
        return captured

    methods = {str(row.get("method") or "") for row in pages}
    if methods == {"ocr"}:
        content_format = "pdf_ocr"
    elif "ocr" in methods:
        content_format = "pdf_mixed"
    else:
        content_format = "pdf_text"
    captured.status = "full"
    captured.content_format = content_format
    captured.pages = pages
    captured.clean_content = "\n".join(
        f"【第{row['page']}页】\n{row['text']}" for row in pages
    )
    captured.failure_reason = None
    captured.failure_stage = None
    captured.message = f"已从官方查看器内存读取并解析 {len(pages)} 页 PDF 正文"
    return captured


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
    loop = asyncio.get_running_loop()
    deadline = loop.time() + get_settings().cebpub_ocr_timeout_seconds

    def remaining(cap: float) -> float:
        return max(0.1, min(cap, deadline - loop.time()))

    for index in range(min(count, 100)):
        if loop.time() >= deadline:
            logger.info("PDF viewer OCR document timeout after %s pages", len(output))
            break
        page_number = index + 1
        if page_numbers is not None and page_number not in page_numbers:
            continue
        try:
            encoded = await asyncio.wait_for(
                frame.evaluate(
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
                ),
                timeout=remaining(15),
            )
            image_bytes = base64.b64decode(encoded) if encoded else b""
            if not image_bytes:
                raise RuntimeError("PDF.js did not render the requested page")
            text = (
                await asyncio.wait_for(
                    asyncio.to_thread(_recognise_image_bytes, image_bytes),
                    timeout=remaining(20),
                )
            ).strip()
        except Exception as exc:  # noqa: BLE001
            if loop.time() >= deadline:
                logger.info("PDF OCR page %s stopped at document deadline", page_number)
                break
            # Older viewer builds may not expose the document object.  The DOM
            # screenshot remains a conservative fallback for those builds.
            try:
                locator = pages.nth(index)
                await locator.scroll_into_view_if_needed(
                    timeout=int(remaining(10) * 1000)
                )
                image_bytes = await locator.screenshot(
                    timeout=int(remaining(20) * 1000)
                )
                text = (
                    await asyncio.wait_for(
                        asyncio.to_thread(_recognise_image_bytes, image_bytes),
                        timeout=remaining(20),
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


async def _pdf_viewer_snapshot(frame) -> dict[str, Any]:
    """Read authoritative PDF.js state from inside the viewer iframe."""
    value = await frame.evaluate(
        """() => {
            const app = window.PDFViewerApplication;
            const pageCount = Number(app?.pdfDocument?.numPages || 0);
            const wrapper = document.querySelector('#errorWrapper');
            const messageNode = document.querySelector('#errorMessage');
            let errorVisible = false;
            if (wrapper) {
                const style = window.getComputedStyle(wrapper);
                errorVisible = !wrapper.hidden
                    && wrapper.getAttribute('aria-hidden') !== 'true'
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
            }
            const errorMessage = errorVisible
                ? String(messageNode?.textContent || wrapper?.textContent || '').trim()
                : '';
            return {
                pageCount,
                renderedPageCount: document.querySelectorAll('.page').length,
                errorVisible,
                errorMessage,
            };
        }"""
    )
    return value if isinstance(value, dict) else {}


async def _wait_for_pdf_viewer_state(frame, *, timeout_ms: int) -> dict[str, Any]:
    """Race document readiness against PDF.js' explicit terminal error UI."""
    deadline = asyncio.get_running_loop().time() + max(0.1, timeout_ms / 1000)
    last: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        last = await _pdf_viewer_snapshot(frame)
        page_count = int(last.get("pageCount") or 0)
        if page_count > 0:
            return {**last, "state": "ready"}
        if bool(last.get("errorVisible")):
            message = str(last.get("errorMessage") or "")[:500]
            reason = _viewer_error_reason(message)
            return {
                **last,
                "state": "error",
                "failure_reason": reason,
                "viewer_error_code": reason,
                "viewer_error_message": message,
            }
        await asyncio.sleep(0.2)
    if int(last.get("renderedPageCount") or 0) > 0:
        return {**last, "state": "rendered"}
    return {**last, "state": "timeout"}


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
    automatic_attempt_ms = min(timeout_ms, 30_000) if headless else timeout_ms
    navigation_timeout_ms = min(automatic_attempt_ms, 15_000)
    iframe_timeout_ms = (
        min(automatic_attempt_ms, 15_000) if headless else automatic_attempt_ms
    )
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
            "about:blank", wait_until="commit", timeout=min(navigation_timeout_ms, 10_000)
        )
        await page.goto(
            detail_url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms,
        )
        try:
            await page.wait_for_selector(
                'iframe[src*="web_pdf"], iframe[src*="pdfjs"]',
                timeout=iframe_timeout_ms,
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
                    status="metadata_only",
                    detail_url=detail_url,
                    message="官方 SPA 未返回当前公告详情，未发现明确验证码或限流证据",
                    failure_reason="outer_detail_unavailable",
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

        pdf_frame = await _wait_for_pdf_frame(page, timeout_ms=iframe_timeout_ms)
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

        document_page_count = 0
        bytes_failure_reason: str | None = None
        ready_timeout_ms = min(
            automatic_attempt_ms,
            get_settings().cebpub_pdf_ready_timeout_seconds * 1_000,
        )
        viewer_state = await _wait_for_pdf_viewer_state(
            pdf_frame, timeout_ms=ready_timeout_ms
        )
        viewer_state_name = str(viewer_state.get("state") or "timeout")
        document_page_count = int(viewer_state.get("pageCount") or 0)
        if viewer_state_name == "error":
            reason = str(
                viewer_state.get("failure_reason") or "pdf_document_unavailable"
            )
            message = str(viewer_state.get("viewer_error_message") or "")[:500]
            if reason == "pdf_invalid_or_corrupt":
                human_message = "官方 PDF.js 查看器报告文件无效或已损坏，已快速跳过"
            elif reason == "official_content_unavailable":
                human_message = "官方 PDF.js 查看器报告文件不存在或已停止提供"
            else:
                human_message = "官方 PDF.js 查看器未能加载文档"
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message=human_message,
                failure_reason=reason,
                failure_stage=_FAILURE_STAGES.get(reason, "pdf_frame"),
                acquisition_path="pdfjs_error_state",
                viewer_error_code=str(
                    viewer_state.get("viewer_error_code") or reason
                ),
                viewer_error_message=message,
                document_page_count=document_page_count,
            )
        if viewer_state_name == "timeout":
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message="PDF.js 在就绪时限内未创建文档对象，按临时失败处理",
                failure_reason="pdf_document_unavailable",
                failure_stage="pdf_frame",
                acquisition_path="pdfjs_readiness",
            )

        if document_page_count:
            pdf_bytes, memory_page_count, bytes_failure_reason = await _read_loaded_pdf_bytes(
                pdf_frame,
                timeout_ms=min(automatic_attempt_ms, 15_000),
                max_bytes=get_settings().cebpub_pdf_max_bytes_mb * 1024 * 1024,
            )
            if pdf_bytes:
                return PublicPdfDetail(
                    status="captured",
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    message="已从官方 PDF.js 查看器取得文档字节，等待本地解析",
                    validation_signals={
                        "uuid_match": id_ok,
                        "outer_title_match": title_ok,
                    },
                    acquisition_path="pdfjs_memory_bytes",
                    document_page_count=memory_page_count or document_page_count,
                    document_bytes=pdf_bytes,
                )
            if bytes_failure_reason == "pdf_too_large":
                return PublicPdfDetail(
                    status="metadata_only",
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    message="PDF 超过本机安全大小上限，未读取正文",
                    failure_reason="pdf_too_large",
                    failure_stage="pdf_bytes",
                    acquisition_path="pdfjs_memory_bytes",
                    document_page_count=document_page_count,
                )
            if bytes_failure_reason == "pdf_invalid_or_corrupt":
                return PublicPdfDetail(
                    status="metadata_only",
                    detail_url=detail_url,
                    pdf_url=pdf_url,
                    message="PDF.js 返回的文档字节不是有效 PDF，已快速跳过",
                    failure_reason="pdf_invalid_or_corrupt",
                    failure_stage="pdf_validation",
                    acquisition_path="pdfjs_memory_bytes",
                    document_page_count=document_page_count,
                )

        try:
            # Only fall back to the viewer's text objects when the already
            # loaded document bytes cannot be obtained.
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
                pdf_frame, timeout_ms=min(automatic_attempt_ms, 12_000)
            )

        extracted_numbers = {int(row["page"]) for row in pages}
        expected_numbers = set(range(1, min(page_count, 100) + 1))
        missing_numbers = expected_numbers - extracted_numbers
        ocr_attempted = False
        if not pages or missing_numbers:
            ocr_attempted = bool(page_count or pages)
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
            reason = bytes_failure_reason or (
                "ocr_failure" if ocr_attempted else "content_unavailable"
            )
            return PublicPdfDetail(
                status="metadata_only",
                detail_url=detail_url,
                pdf_url=pdf_url,
                message="PDF.js 未提供可用文档字节或完整文字层",
                failure_reason=reason,
                failure_stage=_FAILURE_STAGES.get(reason, "pdf_pages"),
                acquisition_path="pdfjs_text_layer_fallback",
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
            acquisition_path="pdfjs_text_layer_fallback",
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
    retryable_reasons = _RETRYABLE_FAILURE_REASONS
    per_attempt_timeout_ms = timeout_ms if not headless else min(timeout_ms, 30_000)

    for collection_attempt in range(2):
        try:
            captured: PublicPdfDetail
            async with broker.acquire(interactive=not headless) as lease:
                if collection_attempt and hasattr(broker, "replace_page"):
                    page = await broker.replace_page(lease)
                else:
                    page = lease.page
                blocked_statuses: set[int] = set()

                def remember_blocked_response(response) -> None:
                    try:
                        host = (urlparse(response.url).hostname or "").lower()
                        official_host = host == "ctbpsp.com" or host.endswith(
                            (".ctbpsp.com", ".cebpubservice.com")
                        ) or host == "cebpubservice.com"
                        if official_host and response.status in {403, 429}:
                            blocked_statuses.add(int(response.status))
                    except Exception:  # noqa: BLE001
                        return

                listener_supported = hasattr(page, "on") and hasattr(
                    page, "remove_listener"
                )
                if listener_supported:
                    page.on("response", remember_blocked_response)
                attempt_count += 1
                try:
                    try:
                        captured = await asyncio.wait_for(
                            _collect_public_pdf_detail(
                                page,
                                detail_url=detail_url,
                                expected_id=expected_id,
                                expected_title=expected_title,
                                expected_project_code=expected_project_code,
                                timeout_ms=per_attempt_timeout_ms,
                                headless=headless,
                            ),
                            timeout=max(15, per_attempt_timeout_ms / 1000 + 2),
                        )
                    except TimeoutError:
                        captured = PublicPdfDetail(
                            status="metadata_only",
                            detail_url=detail_url,
                            message=(
                                "官方查看器在采集时限内未返回文档，已释放工作页并退避重试"
                            ),
                            failure_reason="collector_timeout",
                            failure_stage="pdf_frame",
                        )
                finally:
                    if listener_supported:
                        try:
                            page.remove_listener("response", remember_blocked_response)
                        except Exception:  # noqa: BLE001
                            pass

                captured.browser_reused = lease.reused
                if blocked_statuses and captured.status != "full":
                    captured.document_bytes = None
                    captured.site_blocked = True
                    captured.status = "needs_human_verification"
                    captured.failure_reason = "site_rate_limited"
                    captured.failure_stage = "navigation"
                    captured.message = (
                        f"官方站点返回 {min(blocked_statuses)}，本次详情未继续读取"
                    )

            # PDF 字节已经复制到本机内存。先退出浏览器租约、释放标签页，
            # 再执行坐标文本恢复、OCR 和身份校验，避免本地解析占住浏览器池。
            if captured.status == "captured":
                captured = await _finalise_captured_pdf(
                    captured,
                    expected_title=expected_title,
                    expected_project_code=expected_project_code,
                )

            captured.attempt_count = attempt_count
            captured.duration_ms = int((time.monotonic() - started_at) * 1000)
            captured.acquisition_mode = "managed_chrome"
            captured.browser_state = (
                "ready" if broker.is_connected else "unavailable"
            )
            captured = _apply_failure_policy(captured)
            last_result = captured

            if captured.failure_reason == "browser_closed":
                raise RuntimeError("managed public browser page closed")
            if captured.status == "full":
                return captured
            if captured.status == "needs_human_verification":
                broker.mark_needs_verification()
                captured.browser_state = "needs_verification"
                return captured
            if (
                collection_attempt == 0
                and captured.failure_reason in retryable_reasons
            ):
                await asyncio.sleep(0.35)
                continue
            return captured
        except ManagedPublicBrowserError as exc:
            return _apply_failure_policy(PublicPdfDetail(
                status="needs_human_verification" if not headless else "metadata_only",
                detail_url=detail_url,
                message=f"专用浏览器不可用：{exc}",
                failure_reason="managed_browser_unavailable",
                acquisition_mode="managed_chrome",
                browser_state="unavailable",
                failure_stage="navigation",
                attempt_count=attempt_count,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            ))
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
            last_result = _apply_failure_policy(last_result)
            if collection_attempt == 0:
                await asyncio.sleep(0.35)
                continue
            return last_result
    return last_result or _apply_failure_policy(PublicPdfDetail(
        status="metadata_only",
        detail_url=detail_url,
        message="专用浏览器未返回公告详情",
        failure_reason="managed_browser_unavailable",
        acquisition_mode="managed_chrome",
        browser_state=broker.status()["state"],
        failure_stage="navigation",
        attempt_count=attempt_count,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    ))


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
    captured: PublicPdfDetail
    async with async_playwright() as playwright:
        browser = None
        try:
            browser = await _launch_public_browser(playwright, headless=headless)
            context = await browser.new_context(
                locale="zh-CN", viewport={"width": 1440, "height": 1000}
            )
            page = await context.new_page()
            try:
                captured = await _collect_public_pdf_detail(
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
    if captured.status == "captured":
        captured = await _finalise_captured_pdf(
            captured,
            expected_title=expected_title,
            expected_project_code=expected_project_code,
        )
    return captured


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
        result = await _fetch_managed_public_pdf_detail(
            detail_url=detail_url,
            expected_id=expected_id,
            expected_title=expected_title,
            expected_project_code=expected_project_code,
            timeout_ms=timeout_ms,
            headless=headless,
        )
    else:
        result = await _fetch_legacy_public_pdf_detail(
            detail_url=detail_url,
            expected_id=expected_id,
            expected_title=expected_title,
            expected_project_code=expected_project_code,
            timeout_ms=timeout_ms,
            headless=headless,
        )
    return _apply_failure_policy(result)
