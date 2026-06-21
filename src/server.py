"""FastAPI application factory for the Janus dashboard.

Usage
-----
  from src.server import create_app
  app = create_app(conn, graph, rules_path="rules.yaml", port=8000)

The app exposes:
  GET  /api/queue      — pending approval items
  POST /api/approve/{thread_id}
  POST /api/reject/{thread_id}
  POST /api/undo/{thread_id}
  GET  /api/feed       — SSE live activity stream
  GET  /api/stats      — metric counts
  GET  /api/rules      — read rules.yaml
  POST /api/rules      — write rules.yaml
  GET  /api/activity   — recent action log rows
  GET  /                — serves web/index.html (static SPA)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.queue  import router as queue_router
from src.api.resume import router as resume_router
from src.api.feed   import router as feed_router
from src.api.stats  import router as stats_router
from src.api.rules  import router as rules_router


def create_app(
    conn: sqlite3.Connection,
    graph,
    *,
    rules_path: str = "rules.yaml",
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0", docs_url=None, redoc_url=None)

    # Share state with route handlers via app.state
    app.state.conn       = conn
    app.state.graph      = graph
    app.state.rules_path = rules_path

    # API routes
    app.include_router(queue_router,  prefix="/api")
    app.include_router(resume_router, prefix="/api")
    app.include_router(feed_router,   prefix="/api")
    app.include_router(stats_router,  prefix="/api")
    app.include_router(rules_router,  prefix="/api")

    # Serve the dashboard SPA from web/
    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(web_dir / "index.html"))

        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    # Serve brand assets (logo, icons, favicon)
    assets_dir = Path(__file__).parent.parent / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    return app
