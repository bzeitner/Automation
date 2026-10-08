from __future__ import annotations

import re
from pathlib import Path

from .models import DocumentResult
from .schema import FieldSchema


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(value: str, fallback: str) -> str:
    value = value.strip()
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip(" .-")
    if not value:
        return fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:120]


def proposed_filename(result: DocumentResult, schema: FieldSchema) -> str:
    doc_type = schema.document_types.get(result.document_type)
    type_label = doc_type.filename_label if doc_type else "Unknown"
    components = [
        sanitize_component(result.document_date or "unknown-date", "unknown-date"),
        sanitize_component(result.case_number or "unknown-case", "unknown-case"),
        sanitize_component(result.primary_party or "unknown-party", "unknown-party"),
        sanitize_component(type_label, "Unknown"),
    ]
    return "__".join(components) + result.source_path.suffix.lower()


def collision_safe_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}__{counter:02d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
