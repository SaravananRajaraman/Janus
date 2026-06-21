"""Analyze node — the brain of the Janus pipeline.

One structured LLM call returns: category, intent, summary, rename_to,
confidence.  Destination is resolved deterministically from the category
via rules.yaml — the LLM never decides where files live on disk.

Invariants (never break these):
  • ONE call per file.  Never split the five fields across multiple calls.
  • All LLM access goes through the chain passed in (never import ChatOllama here).
  • Errors are caught and logged; the node returns safe fallback values so
    the watcher keeps running even if Ollama is down.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.schema import AnalysisResult
from src.state import FileState

# ---------------------------------------------------------------------------
# Rename helpers
# ---------------------------------------------------------------------------

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2})_(.+)$")
_SAFE_CHARS_RE = re.compile(r"[^a-z0-9-]")
_MULTI_DASH_RE = re.compile(r"-{2,}")
_SLUG_MAX = 40


def _slugify(text: str) -> str:
    """Convert arbitrary text to a lowercase hyphen-slug."""
    text = text.lower().replace(" ", "-").replace("_", "-")
    text = _SAFE_CHARS_RE.sub("", text)
    text = _MULTI_DASH_RE.sub("-", text)
    return text[:_SLUG_MAX].strip("-")


def _fix_rename(rename_to: str, original_filename: str) -> str:
    """Sanitise the AI-suggested rename and enforce the original extension.

    Handles:
      • Wrong or missing extension  → replaced with original
      • Missing date prefix          → today's YYYY-MM prepended
      • Uppercase / spaces in slug   → lowercased and slugified
      • Empty string                 → fall back to original name slugified
    """
    original_ext = Path(original_filename).suffix  # e.g. ".pdf" — authoritative

    if not rename_to:
        today = datetime.now().strftime("%Y-%m")
        slug = _slugify(Path(original_filename).stem) or "file"
        return f"{today}_{slug}{original_ext}"

    ai_stem = Path(rename_to).stem  # strip whatever ext the model put

    m = _DATE_PREFIX_RE.match(ai_stem)
    if m:
        date_part = m.group(1)                   # already has YYYY-MM
        slug_part = _slugify(m.group(2)) or "file"
        stem = f"{date_part}_{slug_part}"
    else:
        today = datetime.now().strftime("%Y-%m")
        slug = _slugify(ai_stem) or "file"
        stem = f"{today}_{slug}"

    return stem + original_ext


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

_NO_CONTENT_SENTINEL = "(no text content — analyse by filename and extension only)"


def make_analyze_node(
    chain: Any,       # prompt | llm.with_structured_output(AnalysisResult)
    *,
    categories: dict,
    dry_run: bool = False,
):
    """Return a LangGraph node function that performs the AI analysis.

    Args:
        chain:      The chain returned by prompts.make_chain().
        categories: The categories dict from rules.yaml.
        dry_run:    When True, skips the LLM call and returns zeroed-out defaults.

    Returns:
        A node function compatible with StateGraph.add_node().
    """
    category_keys = list(categories.keys())
    _fallback_dest = str(
        Path(
            categories.get("Other", {}).get(
                "destination", "~/Documents/Organised/Other"
            )
        ).expanduser()
    )

    def _resolve_destination(category: str) -> str:
        raw = categories.get(category, {}).get(
            "destination",
            categories.get("Other", {}).get("destination", "~/Documents/Organised/Other"),
        )
        return str(Path(raw).expanduser())

    def node_analyze(state: FileState) -> dict:
        filename = state["filename"]

        # ------------------------------------------------------------------
        # Dry-run: skip the LLM call entirely
        # ------------------------------------------------------------------
        if dry_run:
            print(f"[analyze][dry-run] would analyse: {filename}")
            return {
                "category": "Other",
                "intent": "other",
                "summary": "",
                "rename_to": filename,
                "destination": _fallback_dest,
                "confidence": 0.0,
            }

        # ------------------------------------------------------------------
        # Build prompt input
        # ------------------------------------------------------------------
        content = state.get("content")
        invoke_input = {
            "today": datetime.now().strftime("%Y-%m"),
            "categories": ", ".join(category_keys),
            "filename": filename,
            "extension": Path(filename).suffix or "(none)",
            "content_preview": content if content else _NO_CONTENT_SENTINEL,
        }

        # ------------------------------------------------------------------
        # LLM call — catch all errors so the watcher never dies
        # ------------------------------------------------------------------
        try:
            result: AnalysisResult = chain.invoke(invoke_input)
        except Exception as exc:  # noqa: BLE001
            print(f"[analyze] LLM error for {filename}: {exc}")
            return {
                "category": "Other",
                "intent": "other",
                "summary": "",
                "rename_to": filename,
                "destination": _fallback_dest,
                "confidence": 0.0,
            }

        # ------------------------------------------------------------------
        # Post-process
        # ------------------------------------------------------------------
        # Validate category (model may hallucinate a non-existent one)
        category = result.category if result.category in categories else "Other"

        # Destination: resolved from category, not from the model
        destination = _resolve_destination(category)

        # Rename: sanitise and enforce original extension
        rename_to = _fix_rename(result.rename_to, filename)

        print(
            f"[analyze] {filename}"
            f" → {category} / {result.intent}"
            f" | {rename_to}"
            f" | conf={result.confidence:.2f}"
        )

        return {
            "category": category,
            "intent": result.intent,
            "summary": result.summary,
            "rename_to": rename_to,
            "destination": destination,
            "confidence": result.confidence,
        }

    return node_analyze
