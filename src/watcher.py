"""File-system watcher — the long-lived loop around the graph.

watchdog fires on file-creation events; we kick off one graph run per
file.  The graph is per-file and short-lived; the watcher is the
persistent process.

Boundary: the watcher ONLY detects and dispatches.  It must never
move, rename, or delete files — that is the graph's job (Phase 3+).
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.state import initial_state

# Files whose names start with these prefixes are silently ignored.
_SKIP_PREFIXES: tuple[str, ...] = (".", "~", "desktop.ini", "thumbs.db")


class _FileHandler(FileSystemEventHandler):
    def __init__(self, graph: Any, *, dry_run: bool = False) -> None:
        super().__init__()
        self._graph = graph
        self._dry_run = dry_run
        self._lock = threading.Lock()  # guard against burst duplicates

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return

        path = Path(event.src_path)

        # Skip hidden / system / temp files
        if any(path.name.lower().startswith(p) for p in _SKIP_PREFIXES):
            return

        thread_id = str(uuid.uuid4())
        state = initial_state(
            path=str(path),
            filename=path.name,
            thread_id=thread_id,
        )
        config = {"configurable": {"thread_id": thread_id}}

        label = "[dry-run] " if self._dry_run else ""
        print(f"[watcher] {label}detected: {path.name}  (thread={thread_id[:8]}…)")

        try:
            self._graph.invoke(state, config=config)
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] error processing {path.name}: {exc}")


def start_watcher(
    watch_dirs: list[str],
    graph: Any,
    *,
    dry_run: bool = False,
) -> Observer:
    """Schedule watchdog on every directory and start the observer thread.

    Creates the directory if it does not exist yet (useful for first run).

    Returns the running Observer so the caller can stop it on shutdown.
    """
    handler = _FileHandler(graph, dry_run=dry_run)
    observer = Observer()

    for raw in watch_dirs:
        directory = Path(raw).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        observer.schedule(handler, str(directory), recursive=False)
        print(f"[watcher] watching: {directory}")

    observer.start()
    return observer
