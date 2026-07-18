"""Official PDF/HTML import validation."""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.importers.official_document import (
    MAX_UPLOAD_BYTES,
    OfficialDocumentError,
    extract_official_document,
)


def test_html_import_cleans_active_content_and_validates_title():
    html = """
    <html><head><title>官方公告</title><script>恶意脚本内容</script></head>
    <body><nav>网站导航</nav><article>
      <h1>服务器、数据库、数据库集群软件招标公告</h1>
      <p>招标人：西安航天动力试验技术研究所</p>
      <p>3.1 具备有效营业执照。</p>
    </article></body></html>
    """.encode()
    imported = extract_official_document(
        filename="官方公告.html",
        content_type="text/html",
        data=html,
        expected_title="服务器、数据库、数据库集群软件招标公告",
        expected_project_code=None,
    )

    assert imported.content_format == "html"
    assert imported.identity_basis == "title"
    assert "西安航天动力试验技术研究所" in imported.clean_content
    assert "恶意脚本内容" not in imported.clean_content
    assert "网站导航" not in imported.clean_content
    assert imported.pages[0]["method"] == "html_clean"


def test_import_rejects_identity_mismatch():
    with pytest.raises(OfficialDocumentError) as exc_info:
        extract_official_document(
            filename="其他项目.html",
            content_type="text/html",
            data=b"<html><body>another announcement</body></html>",
            expected_title="服务器项目招标公告",
            expected_project_code="TEST-001",
        )
    assert exc_info.value.code == "identity_mismatch"
    assert exc_info.value.status_code == 422


def test_pdf_import_reads_text_layer_and_validates_project_code():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (TEST-001 Official Tender Notice) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)

    imported = extract_official_document(
        filename="notice.pdf",
        content_type="application/pdf",
        data=output.getvalue(),
        expected_title="不同语言的公告标题",
        expected_project_code="TEST-001",
    )

    assert imported.content_format == "pdf_text"
    assert imported.identity_basis == "project_code"
    assert "TEST-001 Official Tender Notice" in imported.clean_content
    assert imported.pages[0]["method"] == "pdf_text"


def test_import_rejects_extension_content_mismatch_and_large_file():
    with pytest.raises(OfficialDocumentError) as type_error:
        extract_official_document(
            filename="伪装.pdf",
            content_type="application/pdf",
            data=b"<html><body>not a pdf</body></html>",
            expected_title="not a pdf",
            expected_project_code=None,
        )
    assert type_error.value.code == "unsupported_type"

    with pytest.raises(OfficialDocumentError) as size_error:
        extract_official_document(
            filename="过大.html",
            content_type="text/html",
            data=b"x" * (MAX_UPLOAD_BYTES + 1),
            expected_title="x",
            expected_project_code=None,
        )
    assert size_error.value.status_code == 413
