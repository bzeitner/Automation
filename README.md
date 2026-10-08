# Legal Document Processor

A local-first Python command-line tool that extracts configured fields from legal documents, creates safely renamed copies, and writes a reviewable Excel workbook.

The recognized document types and fields live in [`schema/field_schema.yaml`](schema/field_schema.yaml). The source inventory is retained in [`field_list`](field_list).

## Safety model

- Originals are never modified.
- Files with unknown types, missing naming fields, or low-confidence results go to `review/`.
- Identical files are skipped using SHA-256 fingerprints unless `--force` is used.
- OpenRouter is disabled unless explicitly enabled.
- The real `.env`, source legal documents, OCR artifacts, spreadsheets, and generated output are ignored by Git.
- AI output is accepted only when it matches the configured JSON schema.

Review your organization's confidentiality and vendor policies before enabling OpenRouter for client records.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

`rapidocr-onnxruntime` supplies local OCR without requiring a separately installed Tesseract executable. Its first initialization can take longer than subsequent runs.

## Configuration

Edit the ignored `.env` file:

```dotenv
AI_PROVIDER=openrouter
AI_MODEL=openai/gpt-5.1
OPENROUTER_API_KEY=
ALLOW_AI=false
LOCAL_ACCEPT_THRESHOLD=0.90
HUMAN_REVIEW_THRESHOLD=0.85
```

Leave `ALLOW_AI=false` for an entirely local run. When AI is enabled, the tool sends extracted text and no more than three relevant page images. The API key is intentionally unavailable as a command-line argument.

A local-only run still extracts deterministic fields and creates the workbook, but documents with unresolved configured fields are placed in `review/` rather than being represented as complete.

## Usage

Local-only processing:

```bash
document-processor /path/to/intake --no-allow-ai
```

Use the `.env` AI setting:

```bash
document-processor /path/to/intake
```

Override the configured provider or model for one run:

```bash
document-processor /path/to/intake \
  --allow-ai \
  --provider openrouter \
  --model openai/gpt-5.1
```

Useful options:

```text
--output PATH      Output root; defaults to INPUT/output
--schema PATH      Schema path; defaults to schema/field_schema.yaml
--env-file PATH    Configuration file; defaults to .env
--force            Reprocess files already present in the fingerprint manifest
```

## Output

Each run creates:

```text
output/
├── latest.xlsx
├── manifest.json
└── runs/
    └── YYYYMMDD-HHMMSS-microseconds/
        ├── results.xlsx
        ├── renamed/
        └── review/
```

The workbook contains:

- `Documents`: one row per processed or skipped source file.
- `Fields`: one row per populated extracted value, including page, evidence, method, and confidence.
- `Errors`: unsupported files, warnings, configuration problems, and processing failures.

Output names follow:

```text
YYYY-MM-DD__CASE-NUMBER__PRIMARY-PARTY__DOCUMENT-TYPE.ext
```

Collisions receive `__02`, `__03`, and later suffixes. Unknown or uncertain naming components never overwrite another file.

## Tests

```bash
pytest
```

The tests use synthetic text and temporary files; no client documents are committed.
