"""Tests for the analyze node.

Unit tests (no Ollama required)
--------------------------------
  Run anytime:  pytest tests/test_analyze.py -v

Integration tests (Ollama must be running with qwen3.5 pulled)
---------------------------------------------------------------
  pytest tests/test_analyze.py -v -m integration

Before wiring the analyze node into the graph, run the integration tests
against 10 sample fixtures.  If qwen3.5 misbehaves, try llama3.1 and
update rules.yaml accordingly.  Lock the model before moving to Phase 3.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.nodes.analyze import _fix_rename, _slugify, make_analyze_node
from src.schema import AnalysisResult
from src.state import initial_state

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CATEGORIES = {
    "Images":        {"destination": "~/Documents/Organised/Images"},
    "Documents":     {"destination": "~/Documents/Organised/Documents"},
    "Spreadsheets":  {"destination": "~/Documents/Organised/Spreadsheets"},
    "Code":          {"destination": "~/Documents/Organised/Code"},
    "Text":          {"destination": "~/Documents/Organised/Text"},
    "Archives":      {"destination": "~/Documents/Organised/Archives"},
    "Other":         {"destination": "~/Documents/Organised/Other"},
}

DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}_")


def _make_mock_chain(result: AnalysisResult) -> MagicMock:
    """Return a mock chain whose .invoke() returns the given result."""
    chain = MagicMock()
    chain.invoke.return_value = result
    return chain


def _make_state(filename: str, content: str | None = None) -> dict:
    s = initial_state("/tmp/" + filename, filename, "test-thread")
    s["content"] = content
    return s


# ---------------------------------------------------------------------------
# Unit tests — _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_lowercase(self):
        assert _slugify("Hello World") == "hello-world"

    def test_underscores_become_hyphens(self):
        assert _slugify("my_file_name") == "my-file-name"

    def test_special_chars_stripped(self):
        assert _slugify("invoice #42 (Q2)!") == "invoice-42-q2"

    def test_max_length(self):
        long = "a" * 100
        assert len(_slugify(long)) <= 40

    def test_leading_trailing_dashes_stripped(self):
        result = _slugify("---hello---")
        assert not result.startswith("-")
        assert not result.endswith("-")


# ---------------------------------------------------------------------------
# Unit tests — _fix_rename
# ---------------------------------------------------------------------------

class TestFixRename:
    def test_valid_rename_passes_through(self):
        result = _fix_rename("2024-06_aws-invoice.pdf", "original.pdf")
        assert result == "2024-06_aws-invoice.pdf"

    def test_wrong_extension_corrected(self):
        result = _fix_rename("2024-06_invoice.txt", "original.pdf")
        assert result.endswith(".pdf")

    def test_missing_date_prefix_added(self):
        result = _fix_rename("invoice.pdf", "original.pdf")
        assert DATE_PREFIX_RE.match(result), f"No date prefix in: {result}"

    def test_empty_rename_uses_original_name(self):
        result = _fix_rename("", "my_report.docx")
        assert result.endswith(".docx")
        assert DATE_PREFIX_RE.match(result)

    def test_uppercase_slug_lowercased(self):
        result = _fix_rename("2024-06_AWS Invoice Q2.pdf", "original.pdf")
        assert result == result.lower()

    def test_spaces_in_slug_become_hyphens(self):
        result = _fix_rename("2024-06_my invoice.pdf", "original.pdf")
        assert " " not in result

    def test_extension_preserved_for_png(self):
        result = _fix_rename("2025-01_logo.png", "logo.png")
        assert result.endswith(".png")


# ---------------------------------------------------------------------------
# Unit tests — make_analyze_node (mock chain)
# ---------------------------------------------------------------------------

class TestAnalyzeNodeMock:
    def _node(self, result: AnalysisResult, **kwargs):
        chain = _make_mock_chain(result)
        return make_analyze_node(chain, categories=CATEGORIES, **kwargs)

    def test_returns_all_fields(self):
        r = AnalysisResult(
            category="Documents",
            intent="invoice",
            summary="AWS invoice for June 2024.",
            rename_to="2024-06_aws-invoice.pdf",
            confidence=0.92,
        )
        node = self._node(r)
        out = node(_make_state("invoice.pdf"))
        assert out["category"] == "Documents"
        assert out["intent"] == "invoice"
        assert out["confidence"] == pytest.approx(0.92)
        assert DATE_PREFIX_RE.match(out["rename_to"])

    def test_destination_resolved_from_category(self):
        r = AnalysisResult(
            category="Images", intent="image", summary="",
            rename_to="2025-01_logo.png", confidence=0.8,
        )
        node = self._node(r)
        out = node(_make_state("logo.png"))
        assert "Images" in out["destination"] or "Organised" in out["destination"]

    def test_unknown_category_falls_back_to_other(self):
        r = AnalysisResult(
            category="Receipts",   # not in CATEGORIES
            intent="receipt", summary="",
            rename_to="2024-11_receipt.pdf", confidence=0.5,
        )
        node = self._node(r)
        out = node(_make_state("receipt.pdf"))
        assert out["category"] == "Other"

    def test_dry_run_skips_chain(self):
        chain = MagicMock()
        node = make_analyze_node(chain, categories=CATEGORIES, dry_run=True)
        out = node(_make_state("something.txt", content="some text"))
        chain.invoke.assert_not_called()
        assert out["confidence"] == 0.0

    def test_llm_error_returns_safe_fallback(self):
        chain = MagicMock()
        chain.invoke.side_effect = RuntimeError("Ollama not running")
        node = make_analyze_node(chain, categories=CATEGORIES)
        out = node(_make_state("document.pdf"))
        assert out["category"] == "Other"
        assert out["confidence"] == 0.0

    def test_summary_empty_for_non_text(self):
        r = AnalysisResult(
            category="Images", intent="image", summary="",
            rename_to="2025-01_photo.jpg", confidence=0.7,
        )
        node = self._node(r)
        out = node(_make_state("photo.jpg", content=None))
        assert out["summary"] == ""

    def test_summary_populated_for_text(self):
        r = AnalysisResult(
            category="Text", intent="notes",
            summary="Meeting notes from the Q2 planning session.",
            rename_to="2024-06_meeting-notes.txt", confidence=0.85,
        )
        node = self._node(r)
        out = node(_make_state("notes.txt", content="Q2 planning session…"))
        assert out["summary"] != ""


# ---------------------------------------------------------------------------
# Integration tests — requires Ollama with qwen3.5
# ---------------------------------------------------------------------------
#
# Run: pytest tests/test_analyze.py -v -m integration
#
# These 10 fixtures cover the acceptance criterion:
#   "analyze node returns a valid AnalysisResult for 10/10 fixtures"
#
# Tip: run these BEFORE wiring the node into the graph.  If any fixture
# fails due to bad structured output, try llama3.1 and update rules.yaml.

INTEGRATION_FIXTURES = [
    # (filename, content_snippet, expected_category, expected_intent)
    ("AWS_Invoice_June2024.pdf",  None,                         "Documents",    "invoice"),
    ("receipt_amazon_2024.pdf",   None,                         "Documents",    "receipt"),
    ("screenshot_2025.png",       None,                         "Images",       "screenshot"),
    ("notes.txt",                 "Q2 planning session notes.", "Text",         "notes"),
    ("budget_2024.xlsx",          None,                         "Spreadsheets", "data"),
    ("main.py",                   "import os\nprint('hello')", "Code",         "code"),
    ("contract_nda.pdf",          None,                         "Documents",    "contract"),
    ("report_q1.docx",            None,                         "Documents",    "report"),
    ("archive.zip",               None,                         "Archives",     "archive"),
    ("photo_holiday.jpg",         None,                         "Images",       "image"),
]


@pytest.mark.integration
class TestAnalyzeNodeIntegration:
    """Run against real Ollama.  Requires: ollama pull qwen3.5"""

    @pytest.fixture(scope="class")
    def node(self):
        from src.llm import get_llm
        from src.prompts import make_chain as _make_chain

        llm = get_llm(provider="ollama", model="qwen3.5")
        chain = _make_chain(llm, CATEGORIES)
        return make_analyze_node(chain, categories=CATEGORIES)

    @pytest.mark.parametrize(
        "filename, content, expected_cat, expected_intent",
        INTEGRATION_FIXTURES,
        ids=[f[0] for f in INTEGRATION_FIXTURES],
    )
    def test_fixture(self, node, filename, content, expected_cat, expected_intent, tmp_path):
        state = _make_state(filename, content)
        out = node(state)

        # Category and intent must match expected
        assert out["category"] == expected_cat, (
            f"{filename}: expected category '{expected_cat}', got '{out['category']}'"
        )
        assert out["intent"] == expected_intent, (
            f"{filename}: expected intent '{expected_intent}', got '{out['intent']}'"
        )

        # rename_to must follow YYYY-MM_slug.ext
        assert DATE_PREFIX_RE.match(out["rename_to"]), (
            f"{filename}: rename_to has no date prefix: {out['rename_to']!r}"
        )

        # summary must be non-empty for text content, empty otherwise
        if content:
            assert out["summary"], f"{filename}: expected non-empty summary for text file"
        else:
            # For non-text files summary may or may not be populated — just check type
            assert isinstance(out["summary"], str)

        # confidence must be in valid range
        assert 0.0 <= out["confidence"] <= 1.0
