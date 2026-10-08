from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageSequence
from pypdf import PdfReader

from .models import ExtractedDocument, PageContent


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
# Filing stamps sometimes contribute a small text layer to an otherwise scanned
# page. Requiring a fuller page prevents that stamp from suppressing OCR.
MIN_TEXT_CHARACTERS = 250


class ExtractionError(RuntimeError):
    pass


class LocalOCR:
    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise ExtractionError(
                    "Local OCR is unavailable. Install project dependencies first."
                ) from exc
            self._engine = RapidOCR()
        return self._engine

    def read(self, image: Image.Image) -> tuple[str, float]:
        engine = self._get_engine()
        response = engine(np.asarray(image.convert("RGB")))
        result = response[0] if isinstance(response, tuple) else response
        if not result:
            return "", 0.0
        lines: list[str] = []
        scores: list[float] = []
        for item in result:
            if len(item) < 3:
                continue
            lines.append(str(item[1]))
            scores.append(float(item[2]))
        return "\n".join(lines), (sum(scores) / len(scores) if scores else 0.0)


def _render_pdf_page(document: pdfium.PdfDocument, index: int) -> Image.Image:
    return document[index].render(scale=2.0).to_pil().convert("RGB")


def extract_document(path: Path, ocr: LocalOCR | None = None) -> ExtractedDocument:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(f"Unsupported file type: {extension or '(none)'}")
    ocr = ocr or LocalOCR()
    if extension == ".pdf":
        return _extract_pdf(path, ocr)
    return _extract_image(path, ocr)


def _extract_pdf(path: Path, ocr: LocalOCR) -> ExtractedDocument:
    pages: list[PageContent] = []
    warnings: list[str] = []
    try:
        reader = PdfReader(path)
        rendered = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF: {exc}") from exc

    for index, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            text = ""
            warnings.append(f"Page {index + 1}: embedded-text extraction failed: {exc}")
        method = "embedded_text"
        if len(text) < MIN_TEXT_CHARACTERS:
            try:
                ocr_text, score = ocr.read(_render_pdf_page(rendered, index))
                if len(ocr_text.strip()) > len(text):
                    text = ocr_text.strip()
                    method = f"local_ocr:{score:.3f}"
            except Exception as exc:
                warnings.append(f"Page {index + 1}: OCR failed: {exc}")
        pages.append(PageContent(number=index + 1, text=text, extraction_method=method))
    return ExtractedDocument(path=path, pages=pages, warnings=warnings)


def _extract_image(path: Path, ocr: LocalOCR) -> ExtractedDocument:
    pages: list[PageContent] = []
    warnings: list[str] = []
    try:
        with Image.open(path) as source:
            for index, frame in enumerate(ImageSequence.Iterator(source)):
                try:
                    text, score = ocr.read(frame.copy())
                except Exception as exc:
                    text, score = "", 0.0
                    warnings.append(f"Frame {index + 1}: OCR failed: {exc}")
                pages.append(
                    PageContent(
                        number=index + 1,
                        text=text.strip(),
                        extraction_method=f"local_ocr:{score:.3f}",
                    )
                )
    except Exception as exc:
        raise ExtractionError(f"Could not open image: {exc}") from exc
    return ExtractedDocument(path=path, pages=pages, warnings=warnings)


def render_pages_as_jpeg(
    path: Path, page_numbers: Iterable[int], *, max_dimension: int = 1800
) -> list[tuple[int, bytes]]:
    requested = sorted({number for number in page_numbers if number >= 1})
    if not requested:
        return []
    rendered: list[tuple[int, bytes]] = []
    if path.suffix.lower() == ".pdf":
        document = pdfium.PdfDocument(str(path))
        for number in requested:
            if number > len(document):
                continue
            image = _render_pdf_page(document, number - 1)
            image.thumbnail((max_dimension, max_dimension))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
            rendered.append((number, buffer.getvalue()))
        return rendered

    with Image.open(path) as source:
        frames = list(ImageSequence.Iterator(source))
        for number in requested:
            if number > len(frames):
                continue
            image = frames[number - 1].copy().convert("RGB")
            image.thumbnail((max_dimension, max_dimension))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
            rendered.append((number, buffer.getvalue()))
    return rendered
