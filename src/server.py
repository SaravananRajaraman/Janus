"""FastAPI application factory for the Janus dashboard."""
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
from src.api.scan   import router as scan_router


def create_app(
    conn: sqlite3.Connection,
    graph,
    *,
    db_path: str,
    rules_path: str = "rules.yaml",
    auto_organize: dict | None = None,
    scan_config: dict | None = None,
    skip_destinations: set | None = None,
) -> FastAPI:
    app = FastAPI(title="Janus", version="0.1.0", docs_url=None, redoc_url=None)

    # Shared state accessible by all route handlers
    app.state.conn              = conn
    app.state.graph             = graph
    app.state.db_path           = db_path
    app.state.rules_path        = rules_path
    app.state.auto_organize     = auto_organize or {}
    app.state.scan_config       = scan_config or {}
    app.state.skip_destinations = skip_destinations or set()
    app.state.scan_status       = {
        "running": False,
        "found":   0,
        "queued":  0,
        "skipped": 0,
        "errors":  0,
        "current": "",
    }

    @app.get("/api/config", include_in_schema=False)
    def get_config():
        """Expose auto_organize config to the dashboard."""
        ao = app.state.auto_organize
        return {
            "auto_organize": {
                "enabled":         ao.get("enabled", False),
                "min_confidence":  ao.get("min_confidence", 0.85),
                "skip_categories": ao.get("skip_categories", ["Code"]),
            }
        }

    # API routes
    app.include_router(queue_router,  prefix="/api")
    app.include_router(resume_router, prefix="/api")
    app.include_router(feed_router,   prefix="/api")
    app.include_router(stats_router,  prefix="/api")
    app.include_router(rules_router,  prefix="/api")
    app.include_router(scan_router,   prefix="/api")

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
