"""Dupes node — shelve detected duplicates to .organiser/.dupes/."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from src.db import update_action
from src.events import publish
from src.state import FileState


def make_dupes_node(conn: sqlite3.Connection, dupes_path: str, *, dry_run: bool = False):
    def node_dupes(state: FileState) -> dict:
        src      = Path(state["path"])
        dest_dir = Path(dupes_path).expanduser()

        if dry_run:
            print(f"[dupes][dry-run] would shelve duplicate: {src.name}")
            update_action(conn, thread_id=state["thread_id"], status="duplicate")
            publish({"type": "duplicate", "thread_id": state["thread_id"],
                     "filename": state["filename"]})
            return {}

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / src.name
        if dest_file.exists():
            stem, suffix = dest_file.stem, dest_file.suffix
            i = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem}_{i}{suffix}"
                i += 1

        shutil.move(str(src), str(dest_file))

        manifest = dest_dir / "manifest.txt"
        with manifest.open("a") as fh:
            fh.write(f"{datetime.now().isoformat()}  {src.name}  hash={state['file_hash'][:12]}…\n")

        update_action(conn, thread_id=state["thread_id"],
                      status="duplicate", moved_to=str(dest_file))
        publish({"type": "duplicate", "thread_id": state["thread_id"],
                 "filename": state["filename"], "moved_to": str(dest_file)})
        print(f"[dupes] shelved duplicate: {src.name} -> {dest_file}")
        return {}

    return node_dupes
