from __future__ import annotations

import re
from datetime import datetime

from .models import DocumentResult, ExtractedDocument, FieldValue
from .schema import FieldSchema


CASE_NUMBER_RE = re.compile(r"\b\d{2}-\d-\d{5}-\d{2}\b")
COURT_RE = re.compile(
    r"((?:IN THE )?SUPERIOR COURT OF (?:THE STATE OF )?WASHINGTON[^\n]{0,80})",
    re.IGNORECASE,
)
COUNTY_RE = re.compile(r"\b([A-Z][A-Za-z]+) County\b", re.IGNORECASE)
DATE_PATTERNS = [
    re.compile(
        r"^\s*(?:DATED|SIGNED|ORDERED|entered)\s*(?:this)?\s*:?[ ]*"
        r"(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+day\s+of\s+[A-Za-z]+,?\s+20\d{2}|"
        r"[A-Za-z]+\s+\d{1,2},\s+20\d{2}|\d{1,2}/\d{1,2}/20\d{2})",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"\b([A-Za-z]+\s+\d{1,2},\s+20\d{2})\b"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b"),
]
FILED_DATE_RE = re.compile(
    r"(?:ELECTRONICALLY\s*)?(?:RECEIVED\s*AND\s*)?FILED(?:\s+IN\s+OPEN\s+COURT)?"
    r".{0,160}?(\d{1,2}/\d{1,2}/20\d{2}|[A-Z]{3,9}\s+\d(?:\s*\d)?\s+20\d{2})",
    re.IGNORECASE | re.DOTALL,
)
LABELED_DATE_RE = re.compile(
    r"(?:^|\n)\s*Date\s*\n?\s*(\d{1,2}/\d{1,2}/20\d{2})",
    re.IGNORECASE,
)


def _normalize_date(value: str) -> str | None:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)", r"\1", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe\b|\bday of\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    cleaned = re.sub(r"\b([A-Za-z]{3,9}) (\d) (\d) (20\d{2})\b", r"\1 \2\3 \4", cleaned)
    for fmt in (
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _evidence_page(document: ExtractedDocument, match_text: str) -> int | None:
    needle = match_text.casefold()
    for page in document.pages:
        if needle in page.text.casefold():
            return page.number
    return None


def _add_field(
    result: DocumentResult,
    schema: FieldSchema,
    key: str,
    value,
    confidence: float,
    document: ExtractedDocument,
    evidence: str,
) -> None:
    if value in (None, "", []):
        return
    labels = schema.allowed_fields(result.document_type)
    result.fields.append(
        FieldValue(
            key=key,
            label=labels.get(key, key.replace("_", " ").title()),
            value=value,
            source_kind="explicit",
            confidence=confidence,
            page=_evidence_page(document, evidence),
            evidence=evidence[:500],
        )
    )


def _normalized_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def classify_document(
    document: ExtractedDocument, schema: FieldSchema
) -> tuple[str, float, list[str], list[str]]:
    page_texts = [_normalized_for_match(page.text) for page in document.pages]
    candidates: list[tuple[int, int, str]] = []
    embedded: set[str] = set()
    for key, doc_type in schema.document_types.items():
        for alias in sorted(doc_type.aliases, key=len, reverse=True):
            alias_folded = _normalized_for_match(alias)
            if not alias_folded:
                continue
            for page_index, text in enumerate(page_texts):
                position = text.find(alias_folded)
                if position < 0:
                    continue
                # Treat a type as an embedded document only when its title-like
                # alias occurs near the beginning of a page, not in later prose.
                if position <= 1400:
                    embedded.add(key)
                # A file's primary type must be established by the first-page
                # title region. Earlier title matches beat incidental references.
                if page_index == 0 and position <= 1400:
                    candidates.append((position, -len(alias_folded), key))
                break
    if not candidates:
        possible = sorted(embedded)
        return "unknown", 0.0, possible, possible[:5]
    caption_candidates = [candidate for candidate in candidates if candidate[0] <= 300]
    pool = caption_candidates or candidates
    # OCR reading order is not geometrically stable. Within the caption/title
    # region, the most specific (longest) configured title is the safest match.
    pool.sort(key=lambda item: (item[1], item[0], item[2]))
    primary_candidate = pool[0]
    primary = primary_candidate[2]
    confidence = 0.97 if primary_candidate[0] <= 1200 else 0.91
    ranked_candidates = list(
        dict.fromkeys(candidate[2] for candidate in sorted(candidates, key=lambda item: (item[0], item[1])))
    )
    return primary, confidence, sorted(embedded - {primary}), ranked_candidates[:5]


def _clean_party(value: str) -> str | None:
    value = " ".join(value.split()).strip(" ,.;:-")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(
        r"\s*,?\s*(?:a\s*n?|the)\s*(?:individual|Washington.*)$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^(?:v\.?|vs\.?)\s*", "", value, flags=re.IGNORECASE)
    if "," in value and value.count(",") == 1:
        left, right = [part.strip() for part in value.split(",")]
        if left and right and len(right.split()) <= 2:
            value = f"{right} {left}"
    rejected = (
        "court",
        "clerk",
        "case no",
        "cause",
        "declaration",
        "motion",
        "order",
        "notice",
        "attorney",
        "respectfully",
        "washington not",
        "plaintiff",
        "defendant",
    )
    if len(value) < 3 or len(value) > 100 or any(token in value.casefold() for token in rejected):
        return None
    return value


def _caption_parties(text: str) -> tuple[list[str], list[str]]:
    lines = [" ".join(line.split()).strip() for line in text.splitlines()[:120]]
    plaintiffs: list[str] = []
    defendants: list[str] = []

    # Captions normally place the defendant immediately after a standalone v./vs.
    for index, line in enumerate(lines[:90]):
        if line.casefold().strip(". ") not in {"v", "vs"}:
            continue
        for following in lines[index + 1 : index + 11]:
            if not following or following.casefold().startswith(("court", "date", "no.")):
                continue
            if not re.match(r"^[A-Z][A-Z' .\-]{2,}(?:,|$)", following):
                continue
            candidate = _clean_party(following)
            if candidate:
                defendants.append(candidate)
                break
        if defendants:
            break

    for role, destination in (("plaintiff", plaintiffs), ("defendant", defendants)):
        if destination:
            continue
        for index, line in enumerate(lines[:90]):
            line_folded = line.casefold().rstrip("., ")
            if not (line_folded == role or (line_folded.endswith(role) and len(line) <= 120)):
                continue
            same_line = re.sub(role + r".*$", "", line, flags=re.IGNORECASE).strip(" ,")
            candidate = _clean_party(same_line) if same_line else None
            if not candidate:
                for prior in range(index - 1, max(-1, index - 6), -1):
                    candidate = _clean_party(lines[prior])
                    if candidate:
                        break
            if candidate and candidate not in destination:
                destination.append(candidate)

    # Minute sheets often use NAME / Vs / NAME without printing role labels.
    if not defendants:
        for line in lines[:90]:
            compact = line.strip(" ,")
            if re.fullmatch(r"[A-Z][A-Z' .\-]+,[A-Z][A-Z' .\-]+", compact):
                candidate = _clean_party(compact)
                if candidate:
                    defendants.append(candidate)
                    break
    # Cover sheets expose the parties through a Case Title label.
    if not defendants:
        match = re.search(r"Case\s*Title\s+(.{3,100}?)\s+v\.?\s+(.{3,100})(?:\n|$)", text, re.IGNORECASE)
        if match:
            plaintiff = _clean_party(match.group(1))
            defendant = _clean_party(match.group(2))
            if plaintiff:
                plaintiffs.append(plaintiff)
            if defendant:
                defendants.append(defendant)
    prose_match = re.search(
        r"\bDefendant\s+([A-Z][A-Za-z'’\-]+(?:\s*[A-Z][A-Za-z'’\-]+){1,3})(?=\s*[\(\"])",
        text,
    )
    if prose_match:
        prose_party = _clean_party(prose_match.group(1))
        if prose_party and " " in prose_party:
            defendants = [prose_party]
    return plaintiffs, defendants


def _preferred_document_date(text: str) -> tuple[str | None, str | None, float]:
    for pattern in DATE_PATTERNS[:1]:
        match = pattern.search(text)
        if match:
            normalized = _normalize_date(match.group(1))
            if normalized:
                return normalized, match.group(0), 0.96
    match = FILED_DATE_RE.search(text)
    if match:
        normalized = _normalize_date(match.group(1))
        if normalized:
            return normalized, match.group(0), 0.97
    match = LABELED_DATE_RE.search(text[:5000])
    if match:
        normalized = _normalize_date(match.group(1))
        if normalized:
            return normalized, match.group(0), 0.95
    for pattern in DATE_PATTERNS[1:]:
        match = pattern.search(text)
        if match:
            normalized = _normalize_date(match.group(1))
            if normalized:
                return normalized, match.group(0), 0.80
    return None, None, 0.0


def deterministic_extract(
    document: ExtractedDocument, schema: FieldSchema, sha256: str
) -> DocumentResult:
    doc_type, type_confidence, embedded, candidates = classify_document(document, schema)
    type_info = schema.document_types.get(doc_type)
    result = DocumentResult(
        source_path=document.path,
        sha256=sha256,
        document_type=doc_type,
        document_type_label=type_info.label if type_info else "Unknown",
        document_type_confidence=type_confidence,
        candidate_document_types=candidates,
        embedded_document_types=embedded,
        extraction_methods=document.methods,
    )
    text = document.full_text

    case_numbers = CASE_NUMBER_RE.findall(text)
    if case_numbers:
        counts = {number: case_numbers.count(number) for number in set(case_numbers)}
        result.case_number = max(counts, key=lambda number: (counts[number], -case_numbers.index(number)))
        _add_field(result, schema, "case_number", result.case_number, 0.99, document, result.case_number)

    court_match = COURT_RE.search(text)
    if court_match:
        court = " ".join(court_match.group(1).split())
        _add_field(result, schema, "court_name", court, 0.96, document, court_match.group(1))
    county_match = COUNTY_RE.search(text)
    if county_match:
        county = f"{county_match.group(1).title()} County"
        _add_field(result, schema, "court_county", county, 0.95, document, county_match.group(0))

    plaintiffs, defendants = _caption_parties(document.pages[0].text if document.pages else text)
    if plaintiffs:
        _add_field(result, schema, "plaintiff_names", plaintiffs, 0.94, document, plaintiffs[0])
    if defendants:
        party_confidence = 0.94 if " " in defendants[0] else 0.82
        _add_field(
            result,
            schema,
            "defendant_names",
            defendants,
            party_confidence,
            document,
            defendants[0],
        )
    result.all_parties = plaintiffs + [name for name in defendants if name not in plaintiffs]
    if defendants:
        result.primary_party = defendants[0]
    elif result.all_parties:
        result.primary_party = result.all_parties[0]

    normalized_date, date_evidence, date_confidence = _preferred_document_date(text)
    if normalized_date and date_evidence:
        result.document_date = normalized_date
        _add_field(
            result,
            schema,
            "document_date",
            normalized_date,
            date_confidence,
            document,
            date_evidence,
        )

    if type_info:
        _add_field(
            result,
            schema,
            "document_title",
            type_info.label,
            type_confidence,
            document,
            type_info.aliases[0],
        )

    critical_confidences = [
        type_confidence,
        0.99 if result.case_number else 0.0,
        (0.94 if result.primary_party and " " in result.primary_party else 0.82)
        if result.primary_party
        else 0.0,
        date_confidence if result.document_date else 0.0,
    ]
    result.overall_confidence = min(critical_confidences)
    return result
