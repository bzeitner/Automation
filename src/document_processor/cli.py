from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .processor import DocumentProcessor
from .schema import FieldSchema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="document-processor",
        description="Extract, rename, and inventory recognized legal documents.",
    )
    parser.add_argument("input_directory", type=Path, help="Directory to process recursively")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output root (default: INPUT_DIRECTORY/output)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        help="Machine-readable field schema (default: repository or packaged schema)",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--provider", help="Override AI_PROVIDER from .env")
    parser.add_argument("--model", help="Override AI_MODEL from .env")
    parser.add_argument(
        "--allow-ai",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override ALLOW_AI from .env",
    )
    parser.add_argument("--force", action="store_true", help="Reprocess known file fingerprints")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input_directory.is_dir():
        raise SystemExit(f"Input directory does not exist: {args.input_directory}")
    schema_path = args.schema
    if schema_path is None:
        repository_schema = Path("schema/field_schema.yaml")
        packaged_schema = Path(__file__).parent / "data" / "field_schema.yaml"
        schema_path = repository_schema if repository_schema.is_file() else packaged_schema
    if not schema_path.is_file():
        raise SystemExit(f"Schema file does not exist: {schema_path}")
    output_directory = args.output or args.input_directory / "output"
    settings = Settings.load(
        args.env_file,
        provider_override=args.provider,
        model_override=args.model,
        allow_ai_override=args.allow_ai,
    )
    schema = FieldSchema.load(schema_path)
    processor = DocumentProcessor(
        input_directory=args.input_directory,
        output_directory=output_directory,
        schema=schema,
        settings=settings,
        force=args.force,
    )
    workbook, results, errors = processor.run()
    processed = sum(result.status == "processed" for result in results)
    review = sum(result.status == "review" for result in results)
    skipped = sum(result.status == "skipped_duplicate" for result in results)
    print(f"Workbook: {workbook}")
    print(f"Processed: {processed}; review: {review}; skipped duplicates: {skipped}; errors: {len(errors)}")
    return 0
