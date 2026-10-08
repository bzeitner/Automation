import json
from pathlib import Path

from document_processor.config import Settings
from document_processor.models import ExtractedDocument, PageContent
from document_processor.openrouter import OpenRouterClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_openrouter_uses_strict_schema(monkeypatch, schema):
    captured = {}
    ai_value = {
        "document_type": "summons",
        "document_type_confidence": 0.96,
        "embedded_document_types": [],
        "document_date": "2026-07-30",
        "case_number": "26-2-02080-18",
        "primary_party": "Adrienne Barlow",
        "all_parties": ["Adrienne Barlow"],
        "fields": [],
    }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, body=json, timeout=timeout)
        return FakeResponse({"choices": [{"message": {"content": __import__("json").dumps(ai_value)}}]})

    monkeypatch.setattr("document_processor.openrouter.requests.post", fake_post)
    monkeypatch.setattr("document_processor.openrouter.render_pages_as_jpeg", lambda *args, **kwargs: [])
    settings = Settings("openrouter", "openai/test", "secret", True, 0.90, 0.85)
    client = OpenRouterClient(settings, schema)
    document = ExtractedDocument(
        Path("document.pdf"),
        [PageContent(1, "SUMMONS Case No. 26-2-02080-18", "embedded_text")],
    )

    result = client.extract(document)

    assert result.document_type == "summons"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
