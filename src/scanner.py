"""Drive scanner -- walk drives and feed files into the Janus pipeline.

Key design: the scanner daemon thread opens its OWN SQLite connection so it
never contends with the HTTP-handler threads that share app.state.conn.
WAL mode + a busy_timeout let the two connections coexist safely.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import string
import threading
import uuid
from pathlib import Path
from typing import Any

from src.events import publish

# ---------------------------------------------------------------------------
# Windows drive types
# ---------------------------------------------------------------------------
_DRIVE_REMOVABLE = 2
_DRIVE_FIXED     = 3
_SCAN_DRIVE_TYPES = {_DRIVE_FIXED, _DRIVE_REMOVABLE}


def _get_drive_type(path: str) -> int:
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(path)  # type: ignore
    except Exception:
        return _DRIVE_FIXED   # non-Windows fallback


# ---------------------------------------------------------------------------
# System folder names -- always skipped
# ---------------------------------------------------------------------------
_SYSTEM_NAMES: set[str] = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "msocache", "documents and settings", "bootmgr", "boot", "efi",
    "windowsapps", "winsxs",
    "library", "private", "cores", "developer",
    "proc", "sys", "dev", "run", "lost+found",
}


# ---------------------------------------------------------------------------
# Drive enumeration
# ---------------------------------------------------------------------------

def get_drives() -> list[dict]:
    """Return physical/removable drives only (excludes C:, virtual, network)."""
    drives = []
    for letter in string.ascii_uppercase:
        if letter == "C":
            continue
        path = f"{letter}:\\"
        if not os.path.exists(path):
            continue
        if _get_drive_type(path) not in _SCAN_DRIVE_TYPES:
            continue
        info: dict[str, Any] = {"letter": letter, "path": path}
        try:
            u = shutil.disk_usage(path)
            info["total_gb"] = round(u.total / 1e9, 1)
            info["free_gb"]  = round(u.free  / 1e9, 1)
            info["used_gb"]  = round(u.used  / 1e9, 1)
            info["used_pct"] = round(u.used / u.total * 100) if u.total else 0
        except Exception:
            info.update({"total_gb": 0, "free_gb": 0, "used_gb": 0, "used_pct": 0})
        drives.append(info)
    return drives


# ---------------------------------------------------------------------------
# Skip destinations
# ---------------------------------------------------------------------------

def build_skip_destinations(categories: dict, drive_rules: dict) -> set[Path]:
    paths: set[Path] = set()
    for cat_cfg in categories.values():
        dest = cat_cfg.get("destination", "")
        if dest:
            paths.add(Path(dest).expanduser().resolve())
    for dest in drive_rules.values():
        paths.add(Path(dest).expanduser().resolve())
    paths.add(Path(".organiser").resolve())
    return paths


# ---------------------------------------------------------------------------
# Skip predicate
# ---------------------------------------------------------------------------

def _should_skip(path: Path, exclude_paths: list[str], skip_destinations: set[Path]) -> bool:
    for excl in exclude_paths:
        try:
            path.resolve().relative_to(Path(excl).expanduser().resolve())
            return True
        except ValueError:
            pass
    for dest in skip_destinations:
        try:
            path.resolve().relative_to(dest)
            return True
        except (ValueError, OSError):
            pass
    for part in path.parts:
        if part.lower().rstrip("\\/") in _SYSTEM_NAMES:
            return True
    name = path.name.lower()
    if path.is_dir() and (name.startswith(".") or name.startswith("$")):
        return True
    return False


# ---------------------------------------------------------------------------
# File walking
# ---------------------------------------------------------------------------

def scan_files(
    drives: list[str],
    *,
    exclude_paths: list[str] = (),
    skip_destinations: set[Path] | None = None,
    max_files: int = 500,
    min_size_bytes: int = 1024,
) -> list[Path]:
    if skip_destinations is None:
        skip_destinations = set()
    found: list[Path] = []
    for drive in drives:
        root = Path(drive)
        if not root.exists():
            continue
        try:
            for dirpath_str, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
                dp = Path(dirpath_str)
                dirnames[:] = [
                    d for d in dirnames
                    if not _should_skip(dp / d, list(exclude_paths), skip_destinations)
                ]
                for fname in filenames:
                    fpath = dp / fname
                    if fpath.is_symlink():
                        continue
                    try:
                        if fpath.stat().st_size < min_size_bytes:
                            continue
                    except (OSError, PermissionError):
                        continue
                    found.append(fpath)
                    if len(found) >= max_files:
                        return found
        except (PermissionError, OSError):
            continue
    return found


# ---------------------------------------------------------------------------
# Background scan thread
# ---------------------------------------------------------------------------

def start_background_scan(
    drives: list[str],
    graph,
    db_path: str,            # ← path to organiser.db, NOT the shared conn
    scan_status: dict,
    *,
    exclude_paths: list[str] = (),
    skip_destinations: set[Path] | None = None,
    max_files: int = 500,
    min_size_bytes: int = 1024,
    deep_scan: bool = False,
) -> None:
    """Launch a daemon thread that walks drives and feeds files into the graph.

    The thread opens its own SQLite connection (with WAL + busy_timeout) so it
    never deadlocks against the FastAPI HTTP threads that share app.state.conn.
    """

    def _run() -> None:
        # Own connection — isolated from HTTP-handler threads
        conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")   # wait up to 30 s for locks

        scan_status.update({
            "running":   True,
            "found":     0,
            "queued":    0,
            "skipped":   0,
            "errors":    0,
            "current":   "",
            "deep_scan": deep_scan,
        })
        publish({"type": "scan_started", "drives": drives, "max_files": max_files, "deep_scan": deep_scan})
        mode = "deep (content)" if deep_scan else "fast (ext only)"
        print(f"[scanner] starting scan: {drives} | mode={mode} | max={max_files}")

        try:
            # ---- discovery ----
            files = scan_files(
                drives,
                exclude_paths=exclude_paths,
                skip_destinations=skip_destinations,
                max_files=max_files,
                min_size_bytes=min_size_bytes,
            )
            scan_status["found"] = len(files)
            print(f"[scanner] found {len(files)} files")
            publish({"type": "scan_progress", "found": len(files), "queued": 0, "skipped": 0, "errors": 0, "current": ""})

            # ---- process ----
            for i, fpath in enumerate(files):
                if not scan_status.get("running"):
                    print("[scanner] stopped by user")
                    break

                scan_status["current"] = fpath.name
                publish({"type": "scan_current", "filename": fpath.name, "idx": i + 1, "found": len(files)})

                # Path-based dedup (already-processed files)
                try:
                    existing = conn.execute(
                        "SELECT id FROM action_log WHERE path=? AND status NOT IN ('pending','dismissed')",
                        (str(fpath),),
                    ).fetchone()
                except Exception as exc:
                    print(f"[scanner] db error checking {fpath.name}: {exc}")
                    existing = None

                if existing:
                    scan_status["skipped"] += 1
                    file_result = "skipped"
                else:
                    thread_id = f"scan-{uuid.uuid4().hex}"
                    try:
                        graph.invoke(
                            {
                                "path":         str(fpath),
                                "filename":     fpath.name,
                                "thread_id":    thread_id,
                                "skip_content": not deep_scan,
                            },
                            config={"configurable": {"thread_id": thread_id}},
                        )
                        scan_status["queued"] += 1
                        file_result = "queued"
                    except BaseException as exc:
                        exc_name = type(exc).__name__
                        if "Interrupt" in exc_name:
                            # LangGraph paused at the approval node — file IS in queue
                            scan_status["queued"] += 1
                            file_result = "queued"
                        else:
                            print(f"[scanner] error on {fpath.name}: {exc_name}: {exc}")
                            scan_status["errors"] += 1
                            file_result = "error"

                publish({
                    "type":     "scan_file",
                    "filename": fpath.name,
                    "result":   file_result,
                    "idx":      i + 1,
                    "found":    len(files),
                    "queued":   scan_status["queued"],
                    "skipped":  scan_status["skipped"],
                    "errors":   scan_status["errors"],
                })

        except Exception as exc:
            print(f"[scanner] unexpected error: {exc}")
            scan_status["errors"] = scan_status.get("errors", 0) + 1
        finally:
            try:
                conn.close()
            except Exception:
                pass
            scan_status.update({"running": False, "current": ""})
            publish({
                "type":    "scan_complete",
                "found":   scan_status.get("found", 0),
                "queued":  scan_status.get("queued", 0),
                "skipped": scan_status.get("skipped", 0),
                "errors":  scan_status.get("errors", 0),
            })
            print(
                f"[scanner] done -- "
                f"found={scan_status.get('found',0)}, "
                f"queued={scan_status.get('queued',0)}, "
                f"skipped={scan_status.get('skipped',0)}, "
                f"errors={scan_status.get('errors',0)}"
            )

    t = threading.Thread(target=_run, name="janus-scanner", daemon=True)
    t.start()
