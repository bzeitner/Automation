from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceKind = Literal["explicit", "inferred"]


@dataclass(slots=True)
class PageContent:
    number: int
    text: str
    extraction_method: str


@dataclass(slots=True)
class ExtractedDocument:
    path: Path
    pages: list[PageContent]
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def methods(self) -> list[str]:
        return sorted({page.extraction_method for page in self.pages})


@dataclass(slots=True)
class FieldValue:
    key: str
    label: str
    value: Any
    source_kind: SourceKind
    confidence: float
    page: int | None = None
    evidence: str | None = None
    extraction_method: str = "deterministic"


@dataclass(slots=True)
class DocumentResult:
    source_path: Path
    sha256: str
    document_type: str = "unknown"
    document_type_label: str = "Unknown"
    document_type_confidence: float = 0.0
    candidate_document_types: list[str] = field(default_factory=list)
    embedded_document_types: list[str] = field(default_factory=list)
    document_date: str | None = None
    case_number: str | None = None
    primary_party: str | None = None
    all_parties: list[str] = field(default_factory=list)
    fields: list[FieldValue] = field(default_factory=list)
    extraction_methods: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    output_path: Path | None = None
    status: str = "processed"


class AIField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: str | int | float | bool | list[str]
    source_kind: SourceKind
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = Field(default=None, ge=1)
    evidence: str | None = None


class AIExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str
    document_type_confidence: float = Field(ge=0.0, le=1.0)
    embedded_document_types: list[str]
    document_date: str | None
    case_number: str | None
    primary_party: str | None
    all_parties: list[str]
    fields: list[AIField]
