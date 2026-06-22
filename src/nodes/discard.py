"""Discard node -- handle human rejection.

Default: move rejected file to rejected_path (rules.yaml -> settings.rejected_path).
If rejected_path is empty or the source file is gone, it is left in place.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from src.db import update_action
from src.events import publish
from src.state import FileState


def make_discard_node(
    conn: sqlite3.Connection,
    *,
    rejected_path: str = "",
    dry_run: bool = False,
):
    def node_discard(state: FileState) -> dict:
        moved_to = None

        if not dry_run and rejected_path:
            src = Path(state["path"])
            try:
                src_exists = src.exists()
            except OSError:
                src_exists = False   # cloud / network path raised WinError 2 etc.
            if src_exists:
                dest_dir = Path(rejected_path).expanduser()
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / src.name
                if dest_file.exists():
                    stem, suffix = src.stem, src.suffix
                    i = 1
                    while dest_file.exists():
                        dest_file = dest_dir / f"{stem}_{i}{suffix}"
                        i += 1
                shutil.move(str(src), str(dest_file))
                moved_to = str(dest_file)
                print(f"[discard] rejected: {src.name} -> {dest_file}")
            else:
                print(f"[discard] rejected: {state['filename']} -- source not found or not accessible")
        elif dry_run:
            dest = rejected_path or "(in-place)"
            print(f"[discard][dry-run] would move: {state['filename']} -> {dest}")
        else:
            print(f"[discard] rejected: {state['filename']} -- left in place")

        update_action(
            conn,
            thread_id=state["thread_id"],
            status="rejected",
            decision_note=state.get("decision_note"),
            moved_to=moved_to,
        )
        publish({
            "type":      "rejected",
            "thread_id": state["thread_id"],
            "filename":  state["filename"],
            "moved_to":  moved_to,
            "note":      state.get("decision_note"),
        })
        return {}

    return node_discard
