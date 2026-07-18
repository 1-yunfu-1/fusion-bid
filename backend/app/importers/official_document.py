"""Extract official PDF/HTML files without persisting the uploaded binary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from bs4 import UnicodeDammit

from app.cleaners.html_cleaner import clean_html_to_text

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 2_000_000


class OfficialDocumentError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ImportedOfficialDocument:
    filename: str
    content_type: str
    content_format: str
    clean_content: str
    pages: list[dict[str, Any]]
    sha256: str
    size_bytes: int
    identity_basis: str


def _safe_filename(value: str | None) -> str:
    filename = (value or "official-document").replace("\\", "/").rsplit("/", 1)[-1]
    return filename[:255] or "official-document"


def _normalise_identity(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "").lower()


def _clean_text(value: str) -> str:
    value = (value or "").replace("\x00", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _extract_pdf_pages(data: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise OfficialDocumentError(
            "服务端缺少 PDF 文本提取组件，请安装 pypdf 后重试",
            code="pdf_dependency_missing",
            status_code=503,
        ) from exc

    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001
                unlocked = 0
            if not unlocked:
                raise OfficialDocumentError(
                    "不支持有密码的 PDF，请下载未加密的官方文件",
                    code="encrypted_pdf",
                    status_code=422,
                )
        page_count = len(reader.pages)
        if page_count == 0:
            raise OfficialDocumentError("PDF 没有页面", code="empty_pdf", status_code=422)
        if page_count > MAX_PDF_PAGES:
            raise OfficialDocumentError(
                f"PDF 共 {page_count} 页，超过 {MAX_PDF_PAGES} 页安全上限",
                code="too_many_pages",
                status_code=413,
            )
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = _clean_text(page.extract_text() or "")
            if text:
                pages.append({"page": index, "text": text, "method": "pdf_text"})
    except OfficialDocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OfficialDocumentError(
            "无法读取该 PDF，请确认文件完整且来自官方页面",
            code="invalid_pdf",
            status_code=422,
        ) from exc

    if pages:
        return pages, "pdf_text"
    return _ocr_pdf_pages(data, page_count), "pdf_ocr"


def _ocr_pdf_pages(data: bytes, page_count: int) -> list[dict[str, Any]]:
    try:
        import fitz
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise OfficialDocumentError(
            "该 PDF 没有文本层；当前环境未安装扫描件 OCR 组件",
            code="ocr_unavailable",
            status_code=422,
        ) from exc

    pages: list[dict[str, Any]] = []
    try:
        document = fitz.open(stream=data, filetype="pdf")
        if document.page_count != page_count:
            raise ValueError("PDF page count changed during OCR")
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(BytesIO(pixmap.tobytes("png")))
            text = _clean_text(pytesseract.image_to_string(image, lang="chi_sim+eng"))
            if text:
                pages.append({"page": index + 1, "text": text, "method": "ocr"})
    except OfficialDocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OfficialDocumentError(
            "扫描 PDF OCR 失败，请尝试从官方页面下载带文本层的 PDF",
            code="ocr_failed",
            status_code=422,
        ) from exc
    if not pages:
        raise OfficialDocumentError(
            "扫描 PDF 未识别到可用文字",
            code="empty_ocr",
            status_code=422,
        )
    return pages


def _extract_html_pages(data: bytes) -> tuple[list[dict[str, Any]], str]:
    decoded = UnicodeDammit(data, is_html=True).unicode_markup
    if not decoded:
        raise OfficialDocumentError(
            "无法识别 HTML 文件编码", code="invalid_html", status_code=422
        )
    text = _clean_text(clean_html_to_text(decoded))
    if not text:
        raise OfficialDocumentError(
            "HTML 中没有可用的公告正文", code="empty_html", status_code=422
        )
    return [{"page": 1, "text": text, "method": "html_clean"}], "html"


def _validate_identity(
    text: str, *, expected_title: str, expected_project_code: str | None
) -> str:
    body = _normalise_identity(text)
    title = _normalise_identity(expected_title)
    project_code = _normalise_identity(expected_project_code or "")
    if title and title in body:
        return "title"
    if len(project_code) >= 5 and project_code in body:
        return "project_code"
    raise OfficialDocumentError(
        "文件标题和项目编号均无法与当前公告匹配，已拒绝导入以避免串用正文",
        code="identity_mismatch",
        status_code=422,
    )


def extract_official_document(
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    expected_title: str,
    expected_project_code: str | None,
) -> ImportedOfficialDocument:
    safe_name = _safe_filename(filename)
    suffix = "." + safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if not data:
        raise OfficialDocumentError("上传文件为空", code="empty_upload", status_code=422)
    if len(data) > MAX_UPLOAD_BYTES:
        raise OfficialDocumentError(
            "文件超过 20 MB 安全上限", code="upload_too_large", status_code=413
        )

    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        pages, content_format = _extract_pdf_pages(data)
        effective_type = "application/pdf"
    elif suffix in {".html", ".htm"} and not data.startswith(b"%PDF-"):
        pages, content_format = _extract_html_pages(data)
        effective_type = "text/html"
    else:
        raise OfficialDocumentError(
            "仅支持扩展名和内容一致的 PDF、HTML 文件",
            code="unsupported_type",
            status_code=415,
        )

    clean_content = "\n".join(
        f"【第{row['page']}页】\n{row['text']}" for row in pages
    ).strip()
    if len(clean_content) > MAX_EXTRACTED_CHARS:
        raise OfficialDocumentError(
            "提取正文超过 200 万字符安全上限",
            code="content_too_large",
            status_code=413,
        )
    identity_basis = _validate_identity(
        clean_content,
        expected_title=expected_title,
        expected_project_code=expected_project_code,
    )
    return ImportedOfficialDocument(
        filename=safe_name,
        content_type=effective_type,
        content_format=content_format,
        clean_content=clean_content,
        pages=pages,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        identity_basis=identity_basis,
    )
