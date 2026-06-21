"""Execute node — move the file after human approval."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from src.db import update_action
from src.events import publish
from src.state import FileState


def make_execute_node(conn: sqlite3.Connection, *, dry_run: bool = False):
    def node_execute(state: FileState) -> dict:
        src      = Path(state["path"])
        dest_dir = Path(state["destination"]).expanduser()
        dest_file = dest_dir / state["rename_to"]

        if dry_run:
            print(f"[execute][dry-run] would move: {src.name} -> {dest_file}")
            update_action(conn, thread_id=state["thread_id"],
                          status="approved", moved_to=str(dest_file))
            publish({"type": "approved", "thread_id": state["thread_id"],
                     "filename": state["filename"], "moved_to": str(dest_file)})
            return {}

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Collision-safe naming
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            i = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem}_{i}{suffix}"
                i += 1

        shutil.move(str(src), str(dest_file))
        update_action(conn, thread_id=state["thread_id"],
                      status="approved", moved_to=str(dest_file))
        publish({"type": "approved", "thread_id": state["thread_id"],
                 "filename": state["filename"], "moved_to": str(dest_file)})
        print(f"[execute] moved: {src.name} -> {dest_file}")
        return {}

    return node_execute
