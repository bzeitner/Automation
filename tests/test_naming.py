from pathlib import Path

from document_processor.models import DocumentResult
from document_processor.naming import collision_safe_path, proposed_filename, sanitize_component


def test_windows_safe_filename(schema):
    result = DocumentResult(
        source_path=Path("source.PDF"),
        sha256="abc",
        document_type="summons",
        document_type_label="Summons",
        document_date="2026-08-27",
        case_number="26-2-02080-18",
        primary_party='Adrienne: Barlow / Tenant?',
    )
    assert proposed_filename(result, schema) == (
        "2026-08-27__26-2-02080-18__Adrienne-Barlow-Tenant__Summons.pdf"
    )
    assert sanitize_component("CON", "fallback") == "_CON"


def test_collision_suffix(tmp_path):
    first = tmp_path / "name.pdf"
    first.touch()
    second = collision_safe_path(tmp_path, "name.pdf")
    assert second.name == "name__02.pdf"
