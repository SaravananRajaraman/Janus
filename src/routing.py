"""Conditional edge routing functions for the Janus graph.

These are plain Python functions that return the name of the next node.
LangGraph calls them after each conditional branch point.

Graph topology (Phase 3):
    extract -> analyze -> dedupe -> [route_after_dedupe]
        -> "dupes"    (is_duplicate=True)
        -> "approval" (is_duplicate=False)

    approval -> [route_after_approval]
        -> "execute"  (approved=True)
        -> "discard"  (approved=False)
"""
from __future__ import annotations

from src.state import FileState


def route_after_dedupe(state: FileState) -> str:
    """Route to dupes if content hash already seen; otherwise to approval."""
    return "dupes" if state["is_duplicate"] else "approval"


def route_after_approval(state: FileState) -> str:
    """Route to execute on human approval; discard on rejection."""
    return "execute" if state["approved"] else "discard"
