"""Prove OCR changed nothing for documents that never needed it.

``tests/baseline/snapshot.json`` was generated from the v1.1-pre-ocr tag. If
this test fails, current code produces different output for a document that
has no scanned pages — which OCR work must never do.
"""
import json
from pathlib import Path

import pytest

from tests.baseline.generate import build_snapshot

SNAPSHOT_PATH = Path(__file__).parent / "baseline" / "snapshot.json"


@pytest.fixture(scope="module")
def expected() -> dict:
    """The committed pre-OCR output."""
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual() -> dict:
    """The output current code produces for the same fixtures."""
    return build_snapshot()


def test_same_documents(expected: dict, actual: dict):
    assert set(actual) == set(expected)


def test_output_matches_pre_ocr_baseline(expected: dict, actual: dict):
    for name in expected:
        assert actual[name] == expected[name], f"output changed for {name}"
