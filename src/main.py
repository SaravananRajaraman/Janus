"""Janus -- entry point and CLI.

Commands
--------
  janus start            Start watcher + dashboard server + tray icon.
  janus start --dry-run  Analyse files only; no file moves (DB still written).
  janus status           Print a summary of the action log.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

import yaml

from src.checkpoint import make_checkpointer
from src.db import count_by_status, get_db
from src.graph import make_graph
from src.llm import get_llm
from src.prompts import make_chain
from src.watcher import start_watcher

_RULES_FILE = Path("rules.yaml")


def _load_rules(path: Path = _RULES_FILE) -> dict:
    if not path.exists():
        sys.exit(f"[janus] rules file not found: {path.resolve()}")
    with path.open() as fh:
        return yaml.safe_load(fh)


def _start_server(app, port: int) -> None:
    """Run uvicorn in a background daemon thread."""
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="error", access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="janus-server", daemon=True)
    t.start()
    time.sleep(1.2)
    print(f"[janus] dashboard -> http://127.0.0.1:{port}")


def _shutdown(observer, *_) -> None:
    print("\n[janus] shutting down...")
    observer.stop()
    observer.join()
    sys.exit(0)


def cmd_start(args: argparse.Namespace) -> None:
    rules         = _load_rules()
    settings      = rules.get("settings", {})
    db_path       = settings["db_path"]
    watch_dirs    = rules.get("watch", [])
    categories    = rules.get("categories", {})
    dupes_path    = settings.get("dupes_path",    ".organiser/.dupes")
    rejected_path = settings.get("rejected_path", ".organiser/.rejected")
    auto_organize = rules.get("auto_organize",    {})
    drive_rules   = rules.get("drive_rules",      {})
    scan_config   = rules.get("scan",             {})
    port          = int(settings.get("dashboard_port", 8000))

    if not watch_dirs:
        sys.exit("[janus] no watch directories configured in rules.yaml")

    llm_cfg  = rules.get("llm", {})
    provider = llm_cfg.get("provider", "ollama")
    model    = llm_cfg.get("model")

    print(f"[janus] loading LLM: {provider} / {model or 'default'}...")
    try:
        llm   = get_llm(provider=provider, model=model)
        chain = make_chain(llm, categories)
    except Exception as exc:
        sys.exit(f"[janus] failed to load LLM: {exc}")

    conn         = get_db(db_path)
    checkpointer = make_checkpointer(conn)

    graph = make_graph(
        conn,
        chain=chain,
        categories=categories,
        drive_rules=drive_rules,
        checkpointer=checkpointer,
        dupes_path=dupes_path,
        rejected_path=rejected_path,
        auto_organize=auto_organize,
        dry_run=args.dry_run,
    )

    if auto_organize.get("enabled"):
        min_c = auto_organize.get("min_confidence", 0.85)
        skip  = auto_organize.get("skip_categories", [])
        print(f"[janus] auto-organize ON  (>= {min_c:.0%} confidence, skip: {skip or 'none'})")
    else:
        print("[janus] auto-organize OFF  (all files need manual approval)")

    if drive_rules:
        for cat, dest in drive_rules.items():
            print(f"[janus] drive rule: {cat} -> {dest}")

    # Build the set of Janus-owned destination paths to skip during scanning
    from src.scanner import build_skip_destinations
    skip_destinations = build_skip_destinations(categories, drive_rules)

    from src.server import create_app
    app = create_app(
        conn,
        graph,
        db_path=db_path,
        rules_path=str(_RULES_FILE),
        auto_organize=auto_organize,
        scan_config=scan_config,
        skip_destinations=skip_destinations,
    )
    _start_server(app, port)

    observer = start_watcher(watch_dirs, graph, dry_run=args.dry_run)
    signal.signal(signal.SIGTERM, lambda *a: _shutdown(observer))

    try:
        from src.tray import start_tray
        from src.db   import count_by_status as _cbs

        def _pending() -> int:
            try:
                return _cbs(conn).get("pending", 0)
            except Exception:
                return 0

        start_tray(conn, port=port, get_pending=_pending,
                   on_quit=lambda: _shutdown(observer))
    except Exception as exc:
        print(f"[tray] skipped: {exc}")

    dry_label = "  [DRY RUN -- no writes]" if args.dry_run else ""
    print(f"[janus] started.{dry_label}  Ctrl+C to stop.")

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(observer)


def cmd_status(args: argparse.Namespace) -> None:
    rules  = _load_rules()
    conn   = get_db(rules["settings"]["db_path"])
    counts = count_by_status(conn)
    if not counts:
        print("[janus] no actions logged yet.")
        return
    print(f"{'status':<14}  count")
    print("-" * 24)
    for status, n in sorted(counts.items()):
        print(f"  {status:<12}  {n}")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="janus",
        description="Janus -- AI-powered file organiser with human-in-the-loop approval.")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_start = sub.add_parser("start", help="Start watcher + dashboard + tray")
    p_start.add_argument("--dry-run", action="store_true",
        help="Analyse files only; no file moves")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="Show action log summary")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    cli()
