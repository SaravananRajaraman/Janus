"""GET /api/queue — pending approval items."""
from __future__ import annotations
import json
from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/queue")
def get_queue(request: Request):
    from src.db import get_pending_threads
    conn = request.app.state.conn
    rows = get_pending_threads(conn)
    result = []
    for row in rows:
        d = dict(row)
        if d.get("proposal"):
            try:
                d["proposal"] = json.loads(d["proposal"])
            except Exception:
                pass
        result.append(d)
    return result
