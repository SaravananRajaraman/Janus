"""Approval node — the human-in-the-loop interrupt.

This is the threshold: files pause here until a human approves, edits,
or rejects the AI proposal via the Phase 4 dashboard.

The graph is checkpointed before the interrupt fires, so the paused
run survives a process restart. Resuming via:
    graph.invoke(Command(resume=decision), config={"configurable": {"thread_id": tid}})
continues this exact node with the human's decision dict.

Decision dict schema (from the dashboard):
    {
        "approved": bool,
        "note":        str | None,       # optional rejection reason or edit note
        "rename_to":   str | None,       # override the AI suggestion
        "destination": str | None,       # override the destination folder
    }
"""
from __future__ import annotations

from langgraph.types import interrupt

from src.state import FileState


def node_approval(state: FileState) -> dict:
    """Pause the graph and surface the AI proposal to the dashboard.

    Returns the human decision merged back into state.
    """
    decision: dict = interrupt({
        "thread_id":  state["thread_id"],
        "filename":   state["filename"],
        "proposal": {
            "category":    state["category"],
            "intent":      state["intent"],
            "summary":     state["summary"],
            "rename_to":   state["rename_to"],
            "destination": state["destination"],
            "confidence":  state["confidence"],
        },
    })

    return {
        "approved":      decision["approved"],
        "decision_note": decision.get("note"),
        # Dashboard may override rename / destination
        "rename_to":     decision.get("rename_to")   or state["rename_to"],
        "destination":   decision.get("destination") or state["destination"],
    }
