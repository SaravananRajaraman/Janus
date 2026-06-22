"""Analyze node -- the brain of the Janus pipeline.

Two modes
---------
SCAN MODE  (state["skip_content"] = True)
  Extension-only fast path: look up the file extension in the categories dict,
  resolve the destination, and return immediately.  No LLM call, no file
  reading.  Throughput: hundreds of files per second.

WATCHER MODE  (state["skip_content"] = False / absent)
  One structured LLM call returns: category, intent, summary, rename_to,
  confidence.  Destination resolved deterministically from the category.

Destination rules (in priority order):
  1. drive_rules override -- only applied when the file's drive matches
     the rule's target drive (e.g. Video->E:\\Movies only fires for E: files).
  2. Non-C: drive -- destination = <source_drive>\\<Category>
     (files stay on the drive they were found on).
  3. C: drive or files without a drive letter -- use rules.yaml destination.

Invariants (never break these):
  - ONE LLM call per file in watcher mode.
  - All LLM access goes through the chain passed in.
  - Errors are caught; node returns safe fallback values.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.schema import AnalysisResult
from src.state import FileState

# ---------------------------------------------------------------------------
# Rename helpers (watcher mode only)
# ---------------------------------------------------------------------------

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2})_(.+)$")
_SAFE_CHARS_RE  = re.compile(r"[^a-z0-9-]")
_MULTI_DASH_RE  = re.compile(r"-{2,}")
_SLUG_MAX = 40


def _slugify(text: str) -> str:
    text = text.lower().replace(" ", "-").replace("_", "-")
    text = _SAFE_CHARS_RE.sub("", text)
    text = _MULTI_DASH_RE.sub("-", text)
    return text[:_SLUG_MAX].strip("-")


def _fix_rename(rename_to: str, original_filename: str) -> str:
    """Sanitise the AI-suggested rename and enforce the original extension."""
    original_ext = Path(original_filename).suffix

    if not rename_to:
        today = datetime.now().strftime("%Y-%m")
        slug  = _slugify(Path(original_filename).stem) or "file"
        return f"{today}_{slug}{original_ext}"

    ai_stem = Path(rename_to).stem
    m = _DATE_PREFIX_RE.match(ai_stem)
    if m:
        stem = f"{m.group(1)}_{_slugify(m.group(2)) or 'file'}"
    else:
        today = datetime.now().strftime("%Y-%m")
        stem  = f"{today}_{_slugify(ai_stem) or 'file'}"

    return stem + original_ext


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NO_CONTENT_SENTINEL = "(no text content -- analyse by filename and extension only)"

# Drives that are treated as "home" -- use rules.yaml destinations as-is.
_HOME_DRIVES: frozenset[str] = frozenset({"C:"})


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def make_analyze_node(
    chain: Any,
    *,
    categories: dict,
    drive_rules: dict | None = None,
    dry_run: bool = False,
):
    """Return a LangGraph node function that performs the AI analysis.

    Args:
        chain:       The chain returned by prompts.make_chain().
        categories:  The categories dict from rules.yaml.
        drive_rules: Optional category -> absolute destination path override.
                     Applied only when the file's drive matches the rule target's drive.
        dry_run:     Skip the LLM call and return zeroed defaults.
    """
    category_keys  = list(categories.keys())
    _fallback_dest = str(
        Path(
            categories.get("Other", {}).get(
                "destination", "~/Documents/Organised/Other"
            )
        ).expanduser()
    )
    _drive_rules = drive_rules or {}

    # ── Extension → category reverse map (built once at startup) ─────────────
    # Used by the scan-mode fast path: no LLM needed when the extension
    # unambiguously maps to exactly one category.
    _ext_to_category: dict[str, str] = {}
    for cat_name, cat_cfg in categories.items():
        for ext in cat_cfg.get("extensions", []):
            _ext_to_category[ext.lower()] = cat_name

    # ── Destination resolver ──────────────────────────────────────────────────

    def _resolve_destination(category: str, source_path: str | None = None) -> str:
        """Pick the right destination folder.

        Priority:
          1. drive_rules[category]  -- only when source drive == rule drive
          2. Non-home drive         -- <source_drive>\\<Category>
          3. Home / no drive        -- rules.yaml destination
        """
        source_drive = ""
        if source_path:
            try:
                source_drive = Path(source_path).drive.upper()
            except Exception:
                pass

        # 1. drive_rules -- same drive only
        if category in _drive_rules:
            rule_dest  = _drive_rules[category]
            try:
                rule_drive = Path(rule_dest).drive.upper()
            except Exception:
                rule_drive = ""
            if rule_drive and rule_drive == source_drive:
                return str(Path(rule_dest).expanduser())

        # 2. Non-home drive: stay on same drive, folder = category name
        if source_drive and source_drive not in _HOME_DRIVES:
            return str(Path(source_drive + "\\") / category)

        # 3. Home (C:) or no drive: use rules.yaml destination
        raw = categories.get(category, {}).get(
            "destination",
            categories.get("Other", {}).get("destination", "~/Documents/Organised/Other"),
        )
        return str(Path(raw).expanduser())

    # ── Node function ─────────────────────────────────────────────────────────

    def node_analyze(state: FileState) -> dict:
        filename    = state["filename"]
        source_path = state.get("path", "")

        # ── Dry-run ────────────────────────────────────────────────────────
        if dry_run:
            print(f"[analyze][dry-run] would analyse: {filename}")
            return {
                "category":    "Other",
                "intent":      "other",
                "summary":     "",
                "rename_to":   filename,
                "destination": _resolve_destination("Other", source_path),
                "confidence":  0.0,
            }

        # ── SCAN FAST PATH: extension-only, zero LLM calls ─────────────────
        if state.get("skip_content"):
            ext      = Path(filename).suffix.lower()
            category = _ext_to_category.get(ext, "Other")
            destination = _resolve_destination(category, source_path)
            conf     = 1.0 if ext in _ext_to_category else 0.5
            print(
                f"[analyze][ext-fast] {filename}"
                f" -> {category} (ext={ext or 'none'})"
                f" | dest={destination}"
            )
            return {
                "category":    category,
                "intent":      "other",
                "summary":     "",
                "rename_to":   filename,   # keep original name in scan mode
                "destination": destination,
                "confidence":  conf,
            }

        # ── WATCHER MODE: full LLM call ────────────────────────────────────
        content      = state.get("content")
        invoke_input = {
            "today":           datetime.now().strftime("%Y-%m"),
            "categories":      ", ".join(category_keys),
            "filename":        filename,
            "extension":       Path(filename).suffix or "(none)",
            "content_preview": content if content else _NO_CONTENT_SENTINEL,
        }

        try:
            result: AnalysisResult = chain.invoke(invoke_input)
        except Exception as exc:
            print(f"[analyze] LLM error for {filename}: {exc}")
            return {
                "category":    "Other",
                "intent":      "other",
                "summary":     "",
                "rename_to":   filename,
                "destination": _resolve_destination("Other", source_path),
                "confidence":  0.0,
            }

        category    = result.category if result.category in categories else "Other"
        destination = _resolve_destination(category, source_path)
        rename_to   = _fix_rename(result.rename_to, filename)

        print(
            f"[analyze] {filename}"
            f" -> {category} / {result.intent}"
            f" | {rename_to}"
            f" | conf={result.confidence:.2f}"
            f" | dest={destination}"
        )

        return {
            "category":    category,
            "intent":      result.intent,
            "summary":     result.summary,
            "rename_to":   rename_to,
            "destination": destination,
            "confidence":  result.confidence,
        }

    return node_analyze
