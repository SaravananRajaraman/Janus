"""Janus — entry point and CLI.

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
    import uvicorn  # noqa: PLC0415
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="error", access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="janus-server", daemon=True)
    t.start()
    # Give uvicorn a moment so the first browser open works
    time.sleep(1.2)
    print(f"[janus] dashboard → http://127.0.0.1:{port}")


def _shutdown(observer, *_) -> None:
    print("\n[janus] shutting down…")
    observer.stop()
    observer.join()
    sys.exit(0)


def cmd_start(args: argparse.Namespace) -> None:
    rules       = _load_rules()
    settings    = rules.get("settings", {})
    db_path     = settings["db_path"]
    watch_dirs  = rules.get("watch", [])
    categories  = rules.get("categories", {})
    dupes_path  = settings.get("dupes_path", ".organiser/.dupes")
    port        = int(settings.get("dashboard_port", 8000))

    if not watch_dirs:
        sys.exit("[janus] no watch directories configured in rules.yaml")

    llm_cfg  = rules.get("llm", {})
    provider = llm_cfg.get("provider", "ollama")
    model    = llm_cfg.get("model")

    print(f"[janus] loading LLM: {provider} / {model or 'default'}…")
    try:
        llm   = get_llm(provider=provider, model=model)
        chain = make_chain(llm, categories)
    except Exception as exc:
        sys.exit(f"[janus] failed to load LLM: {exc}")

    conn         = get_db(db_path)
    checkpointer = make_checkpointer(db_path)

    graph = make_graph(
        conn,
        chain=chain,
        categories=categories,
        checkpointer=checkpointer,
        dupes_path=dupes_path,
        dry_run=args.dry_run,
    )

    # ── FastAPI dashboard ────────────────────────────────────────
    from src.server import create_app  # noqa: PLC0415
    app = create_app(conn, graph, rules_path=str(_RULES_FILE))
    _start_server(app, port)

    # ── Watchdog ─────────────────────────────────────────────────
    observer = start_watcher(watch_dirs, graph, dry_run=args.dry_run)
    signal.signal(signal.SIGTERM, lambda *a: _shutdown(observer))

    # ── Tray icon (optional) ─────────────────────────────────────
    try:
        from src.tray import start_tray       # noqa: PLC0415
        from src.db   import count_by_status  # noqa: PLC0415, F811

        def _pending() -> int:
            try:
                return count_by_status(conn).get("pending", 0)
            except Exception:
                return 0

        start_tray(conn, port=port, get_pending=_pending,
                   on_quit=lambda: _shutdown(observer))
    except Exception as exc:
        print(f"[tray] skipped: {exc}")

    dry_label = "  [DRY RUN — no writes]" if args.dry_run else ""
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
        description="Janus — AI-powered file organiser with human-in-the-loop approval.")
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
