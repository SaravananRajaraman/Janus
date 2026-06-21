"""FileState — the spine of the Janus graph.

Every node reads from and writes to this TypedDict.
Only return the *keys you mutated*; LangGraph merges the rest.
"""
from __future__ import annotations

from typing import TypedDict


class FileState(TypedDict):
    # --- file identity (set by watcher before graph starts) ---
    path: str           # absolute path of the file
    filename: str       # original name
    thread_id: str      # uuid per file; also the LangGraph thread_id

    # --- content extraction (Phase 1: extract node) ---
    content: str | None  # extracted text, or None for non-text types

    # --- deduplication (Phase 3: dedupe node) ---
    file_hash: str      # SHA-256
    is_duplicate: bool

    # --- AI analysis (Phase 2: analyze node — ONE structured call) ---
    category: str       # Images, Documents, Code, …  (must match rules.yaml key)
    intent: str         # invoice | receipt | screenshot | contract | report | other
    summary: str        # one-line summary (empty string when content is None)
    rename_to: str      # date-prefix slug: 2024-06_invoice-aws.pdf
    destination: str    # resolved target folder (from rules.yaml)
    confidence: float   # 0.0 – 1.0; surfaced in the dashboard

    # --- human-in-the-loop (Phase 3: approval node) ---
    approved: bool | None   # None until human decides
    decision_note: str | None  # optional human override or rejection reason


def initial_state(path: str, filename: str, thread_id: str) -> FileState:
    """Construct a blank FileState ready for the graph entry point."""
    return FileState(
        path=path,
        filename=filename,
        thread_id=thread_id,
        content=None,
        file_hash="",
        is_duplicate=False,
        category="",
        intent="",
        summary="",
        rename_to="",
        destination="",
        confidence=0.0,
        approved=None,
        decision_note=None,
    )
