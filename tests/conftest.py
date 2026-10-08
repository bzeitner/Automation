from pathlib import Path

import pytest

from document_processor.schema import FieldSchema


@pytest.fixture
def schema() -> FieldSchema:
    return FieldSchema.load(Path(__file__).parents[1] / "schema" / "field_schema.yaml")


@pytest.fixture
def sample_order_text() -> str:
    return """SUPERIOR COURT OF WASHINGTON FOR KITSAP COUNTY
PINEWOOD MANOR BREMERTON, a Washington not for profit corporation,
Plaintiff,
v.
ADRIENNE BARLOW, an individual,
Defendant.
Case No. 26-2-02080-18
ORDER GRANTING WRIT OF RESTITUTION
The premises are 280 Sylvan Way, Apt. 105, Bremerton, WA 98310.
DATED October 2, 2026.
"""
