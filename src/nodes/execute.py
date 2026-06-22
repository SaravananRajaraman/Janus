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
        src       = Path(state["path"])
        dest_dir  = Path(state["destination"]).expanduser()
        dest_file = dest_dir / state["rename_to"]

        if dry_run:
            print(f"[execute][dry-run] would move: {src.name} -> {dest_file}")
            update_action(conn, thread_id=state["thread_id"],
                          status="approved", moved_to=str(dest_file))
            publish({"type": "approved", "thread_id": state["thread_id"],
                     "filename": state["filename"], "moved_to": str(dest_file)})
            return {}

        # Check source exists — cloud/network paths (Google Drive, OneDrive) can
        # raise OSError instead of returning False from Path.exists()
        try:
            src_exists = src.exists()
        except OSError:
            src_exists = False

        if not src_exists:
            err = f"Source file not found or not accessible: {src}"
            print(f"[execute] error: {err}")
            update_action(conn, thread_id=state["thread_id"], status="error")
            publish({"type": "error", "thread_id": state["thread_id"],
                     "filename": state["filename"], "detail": err})
            raise FileNotFoundError(err)

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            err = f"Cannot create destination '{dest_dir}': {exc}"
            print(f"[execute] error: {err}")
            update_action(conn, thread_id=state["thread_id"], status="error")
            publish({"type": "error", "thread_id": state["thread_id"],
                     "filename": state["filename"], "detail": err})
            raise OSError(err) from exc

        # Collision-safe naming
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            i = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem}_{i}{suffix}"
                i += 1

        try:
            shutil.move(str(src), str(dest_file))
        except OSError as exc:
            err = f"Move failed: {exc}"
            print(f"[execute] error: {err}")
            update_action(conn, thread_id=state["thread_id"], status="error")
            publish({"type": "error", "thread_id": state["thread_id"],
                     "filename": state["filename"], "detail": err})
            raise OSError(err) from exc

        update_action(conn, thread_id=state["thread_id"],
                      status="approved", moved_to=str(dest_file))
        publish({"type": "approved", "thread_id": state["thread_id"],
                 "filename": state["filename"], "moved_to": str(dest_file)})
        print(f"[execute] moved: {src.name} -> {dest_file}")
        return {}

    return node_execute
