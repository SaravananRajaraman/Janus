"""POST /api/approve, /api/reject, /api/undo, /api/dismiss — resume paused graphs."""
from __future__ import annotations
import shutil
import traceback
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

def _check_checkpoint(graph, thread_id: str):
    """Raise 409 if no live checkpoint exists for this thread_id.

    This catches two cases:
      • Stale DB rows from previous runs (checkpoint was never saved or was wiped)
      • Race window: file is still being processed (graph hasn't hit interrupt yet)
    """
    try:
        snap = graph.get_state({"configurable": {"thread_id": thread_id}})
        if not snap or not snap.values:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No active checkpoint for this file. "
                    "It may still be processing (refresh in a moment) "
                    "or it's a stale entry — use Dismiss to clear it."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=409,
            detail="Could not verify checkpoint — file may still be processing.",
        )


# ── request bodies ────────────────────────────────────────────────────────────

class ApproveBody(BaseModel):
    rename_to:   Optional[str] = None
    destination: Optional[str] = None
    note:        Optional[str] = None

class RejectBody(BaseModel):
    note: Optional[str] = None


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("/approve/{thread_id}")
def approve(thread_id: str, body: ApproveBody, request: Request):
    graph = request.app.state.graph
    _check_checkpoint(graph, thread_id)
    try:
        graph.invoke(
            Command(resume={
                "approved":    True,
                "note":        body.note,
                "rename_to":   body.rename_to,
                "destination": body.destination,
            }),
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.post("/reject/{thread_id}")
def reject(thread_id: str, body: RejectBody, request: Request):
    graph = request.app.state.graph
    _check_checkpoint(graph, thread_id)
    try:
        graph.invoke(
            Command(resume={"approved": False, "note": body.note}),
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.post("/dismiss/{thread_id}")
def dismiss(thread_id: str, request: Request):
    """Remove a stale pending entry from the queue without resuming the graph."""
    conn = request.app.state.conn
    conn.execute(
        "UPDATE action_log SET status='dismissed' WHERE thread_id=? AND status='pending'",
        (thread_id,),
    )
    conn.commit()
    from src.events import publish
    publish({"type": "dismissed", "thread_id": thread_id})
    return {"ok": True}


@router.post("/undo/{thread_id}")
def undo(thread_id: str, request: Request):
    conn = request.app.state.conn
    row = conn.execute(
        "SELECT path, moved_to FROM action_log WHERE thread_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    if not row or not row["moved_to"]:
        raise HTTPException(status_code=404, detail="Nothing to undo")
    src, dst = Path(row["moved_to"]), Path(row["path"])
    if not src.exists():
        raise HTTPException(status_code=409, detail=f"File not at expected location: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    conn.execute(
        "UPDATE action_log SET status='undone' WHERE thread_id=? AND status='approved'",
        (thread_id,),
    )
    conn.commit()
    from src.events import publish
    publish({"type": "undone", "thread_id": thread_id, "filename": dst.name})
    return {"ok": True, "restored_to": str(dst)}
