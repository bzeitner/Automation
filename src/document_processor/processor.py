from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .config import Settings
from .deterministic import deterministic_extract
from .extraction import SUPPORTED_EXTENSIONS, ExtractionError, extract_document
from .models import DocumentResult, FieldValue
from .naming import collision_safe_path, proposed_filename
from .openrouter import AIExtractionError, OpenRouterClient
from .reporting import write_workbook
from .schema import FieldSchema


EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "output",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_files(input_directory: Path, output_directory: Path) -> tuple[list[Path], list[Path]]:
    supported: list[Path] = []
    unsupported: list[Path] = []
    output_resolved = output_directory.resolve()
    for path in sorted(input_directory.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if any(part in EXCLUDED_DIRECTORY_NAMES or part.startswith(".") for part in path.parts):
            continue
        try:
            if path.resolve().is_relative_to(output_resolved):
                continue
        except (OSError, ValueError):
            pass
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            supported.append(path)
        else:
            unsupported.append(path)
    return supported, unsupported


def _load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_manifest(path: Path, manifest: dict[str, dict]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _find_field_confidence(result: DocumentResult, key: str, fallback: float) -> float:
    confidences = [field.confidence for field in result.fields if field.key == key]
    return max(confidences, default=fallback)


def _has_unresolved_schema_fields(result: DocumentResult, schema: FieldSchema) -> bool:
    doc_type = schema.document_types.get(result.document_type)
    if not doc_type or not doc_type.fields:
        return result.document_type == "unknown"
    extracted_keys = {field.key for field in result.fields}
    return not set(doc_type.fields).issubset(extracted_keys)


def _merge_ai(result: DocumentResult, ai, schema: FieldSchema) -> None:
    if result.case_number and ai.case_number and result.case_number != ai.case_number:
        result.review_reasons.append(
            f"AI case number {ai.case_number!r} disagreed with deterministic value {result.case_number!r}"
        )
    elif not result.case_number:
        result.case_number = ai.case_number

    if ai.document_type != "unknown" and (
        result.document_type == "unknown"
        or ai.document_type_confidence > result.document_type_confidence
    ):
        result.document_type = ai.document_type
        doc_type = schema.document_types[ai.document_type]
        result.document_type_label = doc_type.label
        result.document_type_confidence = ai.document_type_confidence
    result.embedded_document_types = sorted(
        (set(result.embedded_document_types) | set(ai.embedded_document_types)) - {result.document_type}
    )
    if not result.document_date:
        result.document_date = ai.document_date
    if not result.primary_party:
        result.primary_party = ai.primary_party
    result.all_parties = list(dict.fromkeys([*result.all_parties, *ai.all_parties]))

    existing = {(field.key, json.dumps(field.value, sort_keys=True, default=str)) for field in result.fields}
    for field in ai.fields:
        identity = (field.key, json.dumps(field.value, sort_keys=True, default=str))
        if identity in existing:
            continue
        result.fields.append(
            FieldValue(
                key=field.key,
                label=field.label,
                value=field.value,
                source_kind=field.source_kind,
                confidence=field.confidence,
                page=field.page,
                evidence=field.evidence,
                extraction_method="openrouter",
            )
        )
        existing.add(identity)
    if "openrouter" not in result.extraction_methods:
        result.extraction_methods.append("openrouter")


def _finalize_review_status(result: DocumentResult, settings: Settings) -> None:
    confidence_parts = [
        result.document_type_confidence,
        _find_field_confidence(result, "case_number", 0.0 if not result.case_number else 0.85),
        _find_field_confidence(result, "document_date", 0.0 if not result.document_date else 0.85),
        max(
            _find_field_confidence(result, "defendant_names", 0.0),
            _find_field_confidence(result, "primary_party", 0.0 if not result.primary_party else 0.85),
        ),
    ]
    result.overall_confidence = min(confidence_parts)
    missing = []
    if not result.document_date:
        missing.append("document_date")
    if not result.case_number:
        missing.append("case_number")
    if not result.primary_party:
        missing.append("primary_party")
    if result.document_type == "unknown":
        missing.append("document_type")
    if missing:
        result.review_reasons.append("Missing or unresolved naming fields: " + ", ".join(missing))
    if result.overall_confidence < settings.human_review_threshold:
        result.review_reasons.append(
            f"Overall confidence {result.overall_confidence:.2f} is below review threshold "
            f"{settings.human_review_threshold:.2f}"
        )
    result.review_reasons = list(dict.fromkeys(result.review_reasons))
    result.needs_review = bool(result.review_reasons)


class DocumentProcessor:
    def __init__(
        self,
        *,
        input_directory: Path,
        output_directory: Path,
        schema: FieldSchema,
        settings: Settings,
        force: bool = False,
    ) -> None:
        self.input_directory = input_directory.resolve()
        self.output_directory = output_directory.resolve()
        self.schema = schema
        self.settings = settings
        self.force = force

    def run(self) -> tuple[Path, list[DocumentResult], list[dict[str, str]]]:
        self.output_directory.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_directory = self.output_directory / "runs" / run_id
        renamed_directory = run_directory / "renamed"
        review_directory = run_directory / "review"
        renamed_directory.mkdir(parents=True)
        review_directory.mkdir(parents=True)

        manifest_path = self.output_directory / "manifest.json"
        manifest = _load_manifest(manifest_path)
        supported, unsupported = discover_files(self.input_directory, self.output_directory)
        results: list[DocumentResult] = []
        errors: list[dict[str, str]] = [
            {
                "document_id": "",
                "source_path": str(path),
                "category": "unsupported_file_type",
                "message": f"Unsupported extension: {path.suffix or '(none)'}",
            }
            for path in unsupported
        ]
        ai_client = None
        if self.settings.allow_ai:
            try:
                ai_client = OpenRouterClient(self.settings, self.schema)
            except ValueError as exc:
                errors.append(
                    {
                        "document_id": "",
                        "source_path": str(self.input_directory),
                        "category": "ai_configuration",
                        "message": str(exc),
                    }
                )

        for path in supported:
            digest = sha256_file(path)
            if digest in manifest and not self.force:
                prior = manifest[digest]
                results.append(
                    DocumentResult(
                        source_path=path,
                        sha256=digest,
                        document_type=prior.get("document_type", "unknown"),
                        document_type_label=prior.get("document_type_label", "Unknown"),
                        document_date=prior.get("document_date"),
                        case_number=prior.get("case_number"),
                        primary_party=prior.get("primary_party"),
                        output_path=Path(prior["output_path"]) if prior.get("output_path") else None,
                        status="skipped_duplicate",
                    )
                )
                continue

            try:
                extracted = extract_document(path)
                result = deterministic_extract(extracted, self.schema, digest)
                for warning in extracted.warnings:
                    errors.append(
                        {
                            "document_id": "",
                            "source_path": str(path),
                            "category": "extraction_warning",
                            "message": warning,
                        }
                    )
                needs_ai = (
                    result.overall_confidence < self.settings.local_accept_threshold
                    or _has_unresolved_schema_fields(result, self.schema)
                )
                if needs_ai:
                    if ai_client:
                        try:
                            _merge_ai(result, ai_client.extract(extracted), self.schema)
                        except AIExtractionError as exc:
                            result.review_reasons.append(str(exc))
                            errors.append(
                                {
                                    "document_id": "",
                                    "source_path": str(path),
                                    "category": "ai_extraction",
                                    "message": str(exc),
                                }
                            )
                    else:
                        result.review_reasons.append(
                            "Configured fields remain unresolved and AI was unavailable or disabled"
                        )
                _finalize_review_status(result, self.settings)
                target_directory = review_directory if result.needs_review else renamed_directory
                destination = collision_safe_path(
                    target_directory, proposed_filename(result, self.schema)
                )
                shutil.copy2(path, destination)
                result.output_path = destination
                result.status = "review" if result.needs_review else "processed"
                results.append(result)
                manifest[digest] = {
                    "source_path": str(path),
                    "output_path": str(destination),
                    "document_type": result.document_type,
                    "document_type_label": result.document_type_label,
                    "document_date": result.document_date,
                    "case_number": result.case_number,
                    "primary_party": result.primary_party,
                    "processed_at": datetime.now().isoformat(timespec="seconds"),
                }
            except (ExtractionError, OSError, ValueError) as exc:
                errors.append(
                    {
                        "document_id": "",
                        "source_path": str(path),
                        "category": "processing_failure",
                        "message": str(exc),
                    }
                )

        workbook_path = run_directory / "results.xlsx"
        write_workbook(results, errors, workbook_path)
        shutil.copy2(workbook_path, self.output_directory / "latest.xlsx")
        _write_manifest(manifest_path, manifest)
        return workbook_path, results, errors
