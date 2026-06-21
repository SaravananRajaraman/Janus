"""Discard node — handle human rejection. File stays in place."""
from __future__ import annotations

import sqlite3

from src.db import update_action
from src.events import publish
from src.state import FileState


def make_discard_node(conn: sqlite3.Connection):
    def node_discard(state: FileState) -> dict:
        update_action(conn, thread_id=state["thread_id"], status="rejected",
                      decision_note=state.get("decision_note"))
        publish({"type": "rejected", "thread_id": state["thread_id"],
                 "filename": state["filename"],
                 "note": state.get("decision_note")})
        print(f"[discard] rejected: {state['filename']} — left in place")
        return {}
    return node_discard
