from pathlib import Path

from document_processor.deterministic import deterministic_extract
from document_processor.models import ExtractedDocument, PageContent


def test_extracts_critical_fields(schema, sample_order_text):
    document = ExtractedDocument(
        path=Path("order.pdf"),
        pages=[PageContent(1, sample_order_text, "embedded_text")],
    )
    result = deterministic_extract(document, schema, "abc")

    assert result.document_type == "order_granting_writ_restitution"
    assert result.case_number == "26-2-02080-18"
    assert result.document_date == "2026-10-02"
    assert result.primary_party == "ADRIENNE BARLOW"
    assert result.overall_confidence >= 0.90


def test_recognizes_embedded_document_types(schema):
    document = ExtractedDocument(
        path=Path("declaration.pdf"),
        pages=[
            PageContent(1, "DECLARATION OF JORDAN MCFEELY", "embedded_text"),
            PageContent(2, "U.S. Postal Service Certified Mail Receipt", "embedded_text"),
        ],
    )
    result = deterministic_extract(document, schema, "abc")

    assert result.document_type == "fact_declaration"
    assert "certified_mail_receipt" in result.embedded_document_types


def test_ocr_spacing_does_not_promote_later_reference(schema):
    text = """FILED
JUL 31 2026
SUPERIORCOURTOFWASHINGTONFORKITSAPCOUNTY
PINEWOODMANORBREMERTON,a
Washington not for profit corporation,
MOTIONFORORDERTOSHOW
CAUSE
Plaintiff,
V.
ADRIENNEBARLOW,anindividual,
Defendant.
This motion cites the Declaration of J'Vonne Hendricks.
"""
    document = ExtractedDocument(
        path=Path("motion.pdf"),
        pages=[PageContent(1, text, "local_ocr:0.98")],
    )
    result = deterministic_extract(document, schema, "abc")

    assert result.document_type == "motion_order_show_cause"
    assert result.primary_party == "ADRIENNEBARLOW"
    assert result.document_date == "2026-07-31"
