"""GET /api/stats — dashboard metric cards."""
from __future__ import annotations
from fastapi import APIRouter, Request
from src.db import count_by_status

router = APIRouter()

@router.get("/stats")
def get_stats(request: Request):
    conn = request.app.state.conn
    counts = count_by_status(conn)
    moved_today = conn.execute(
        "SELECT COUNT(*) FROM action_log WHERE status='approved' AND date(created_at)=date('now')"
    ).fetchone()[0]
    return {
        "pending":    counts.get("pending",   0),
        "moved_today": moved_today,
        "duplicates": counts.get("duplicate", 0),
        "rejections": counts.get("rejected",  0),
        "total":      sum(counts.values()),
    }
