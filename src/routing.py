"""Conditional edge routing functions for the Janus graph.

These are plain Python functions that return the name of the next node.
LangGraph calls them after each conditional branch point.

Graph topology:
    extract -> analyze -> update_proposal -> dedupe -> [route_after_dedupe]
        -> "dupes"        (is_duplicate=True)
        -> "auto_approve" (is_duplicate=False AND auto-organize criteria met)
        -> "approval"     (is_duplicate=False, human review required)

    approval -> [route_after_approval]
        -> "execute"  (approved=True)
        -> "discard"  (approved=False)
"""
from __future__ import annotations

from src.state import FileState


def route_after_approval(state: FileState) -> str:
    """Route to execute on human approval; discard on rejection."""
    return "execute" if state["approved"] else "discard"


def make_route_after_dedupe(auto_organize=None):
    """Return a routing function that respects the auto_organize config.

    auto_organize keys (all optional):
        enabled         bool   - if False, always route to human approval
        min_confidence  float  - minimum confidence to auto-approve (default 0.85)
        skip_categories list   - categories that always require human review
    """
    cfg = auto_organize or {}

    def route_after_dedupe(state: FileState) -> str:
        if state["is_duplicate"]:
            return "dupes"

        if cfg.get("enabled"):
            min_conf  = float(cfg.get("min_confidence", 0.85))
            skip_cats = [c.lower() for c in cfg.get("skip_categories", [])]
            confidence = float(state.get("confidence") or 0.0)
            category   = (state.get("category") or "").lower()

            if confidence >= min_conf and category not in skip_cats:
                return "auto_approve"

        return "approval"

    return route_after_dedupe
