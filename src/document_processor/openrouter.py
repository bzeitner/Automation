from __future__ import annotations

import base64
import json
from typing import Any

import requests
from pydantic import ValidationError

from .config import Settings
from .extraction import render_pages_as_jpeg
from .models import AIExtraction, ExtractedDocument
from .schema import FieldSchema


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIExtractionError(RuntimeError):
    pass


def _response_schema(schema: FieldSchema) -> dict[str, Any]:
    document_types = sorted(["unknown", *schema.document_types.keys()])
    field_keys = sorted(schema.all_allowed_field_keys())
    field_item = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": field_keys},
            "label": {"type": "string"},
            "value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "source_kind": {"type": "string", "enum": ["explicit", "inferred"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "page": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "evidence": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["key", "label", "value", "source_kind", "confidence", "page", "evidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": document_types},
            "document_type_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "embedded_document_types": {
                "type": "array",
                "items": {"type": "string", "enum": document_types},
            },
            "document_date": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "case_number": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "primary_party": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "all_parties": {"type": "array", "items": {"type": "string"}},
            "fields": {"type": "array", "items": field_item},
        },
        "required": [
            "document_type",
            "document_type_confidence",
            "embedded_document_types",
            "document_date",
            "case_number",
            "primary_party",
            "all_parties",
            "fields",
        ],
        "additionalProperties": False,
    }


def _prompt(schema: FieldSchema, document: ExtractedDocument) -> str:
    type_lines = []
    for key, doc_type in schema.document_types.items():
        fields = schema.allowed_fields(key)
        type_lines.append(
            f"- {key}: {doc_type.label}; fields: " + ", ".join(sorted(fields))
        )
    page_text = "\n\n".join(
        f"--- PAGE {page.number} ---\n{page.text}" for page in document.pages
    )
    return (
        "Extract fields from this legal document. Use only the document types and field keys listed below. "
        "Choose the primary filing type for the whole file and list exhibit types under embedded_document_types. "
        "Use ISO YYYY-MM-DD dates. Prefer defendant/respondent/tenant as primary_party. "
        "Mark directly labeled values explicit and prose-derived facts inferred. Do not guess. "
        "Evidence must be a short verbatim passage from the supplied page.\n\n"
        "ALLOWED TYPES AND FIELDS\n"
        + "\n".join(type_lines)
        + "\n\nDOCUMENT TEXT\n"
        + page_text[:100_000]
    )


class OpenRouterClient:
    def __init__(self, settings: Settings, schema: FieldSchema) -> None:
        settings.validate_for_ai()
        self.settings = settings
        self.schema = schema

    def extract(self, document: ExtractedDocument) -> AIExtraction:
        prompt = _prompt(self.schema, document)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        candidate_pages = [
            page.number
            for page in document.pages
            if page.number == 1 or page.extraction_method.startswith("local_ocr") or not page.text
        ][:3]
        for page_number, image_bytes in render_pages_as_jpeg(document.path, candidate_pages):
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
            content.append({"type": "text", "text": f"The preceding image is page {page_number}."})

        payload = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "legal_document_extraction",
                    "strict": True,
                    "schema": _response_schema(self.schema),
                },
            },
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                        "X-OpenRouter-Title": "Legal Document Processor",
                    },
                    json=payload,
                    timeout=180,
                )
                response.raise_for_status()
                content_value = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content_value) if isinstance(content_value, str) else content_value
                extraction = AIExtraction.model_validate(parsed)
                self._validate_allowed_values(extraction)
                return extraction
            except (requests.RequestException, KeyError, TypeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    payload["messages"].append(
                        {
                            "role": "user",
                            "content": "Your prior response was invalid. Return only a response matching the JSON schema.",
                        }
                    )
        raise AIExtractionError(f"OpenRouter extraction failed after two attempts: {last_error}")

    def _validate_allowed_values(self, extraction: AIExtraction) -> None:
        if extraction.document_type != "unknown" and extraction.document_type not in self.schema.document_types:
            raise ValueError("AI returned an unknown document type")
        allowed_types = {"unknown", *self.schema.document_types}
        if any(value not in allowed_types for value in extraction.embedded_document_types):
            raise ValueError("AI returned an unknown embedded document type")
        allowed_fields = self.schema.all_allowed_field_keys()
        if any(field.key not in allowed_fields for field in extraction.fields):
            raise ValueError("AI returned a field outside the configured schema")
