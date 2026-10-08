from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class DocumentType:
    key: str
    label: str
    filename_label: str
    aliases: tuple[str, ...]
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class FieldSchema:
    version: int
    required_naming_fields: tuple[str, ...]
    common_fields: dict[str, str]
    document_types: dict[str, DocumentType]

    @classmethod
    def load(cls, path: Path) -> "FieldSchema":
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        document_types = {
            key: DocumentType(
                key=key,
                label=value["label"],
                filename_label=value["filename_label"],
                aliases=tuple(value.get("aliases", [])),
                fields=dict(value.get("fields", {})),
            )
            for key, value in raw["document_types"].items()
        }
        return cls(
            version=int(raw["schema_version"]),
            required_naming_fields=tuple(raw["required_naming_fields"]),
            common_fields=dict(raw["common_fields"]),
            document_types=document_types,
        )

    def allowed_fields(self, document_type: str) -> dict[str, str]:
        fields = dict(self.common_fields)
        doc_type = self.document_types.get(document_type)
        if doc_type:
            fields.update(doc_type.fields)
        return fields

    def all_allowed_field_keys(self) -> set[str]:
        keys = set(self.common_fields)
        for doc_type in self.document_types.values():
            keys.update(doc_type.fields)
        return keys
