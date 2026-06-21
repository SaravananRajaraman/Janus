"""POST /api/approve, /api/reject, /api/undo — resume paused graphs."""
from __future__ import annotations
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel

router = APIRouter()


class ApproveBody(BaseModel):
    rename_to:   Optional[str] = None
    destination: Optional[str] = None
    note:        Optional[str] = None


class RejectBody(BaseModel):
    note: Optional[str] = None


@router.post("/approve/{thread_id}")
def approve(thread_id: str, body: ApproveBody, request: Request):
    graph = request.app.state.graph
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
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.post("/reject/{thread_id}")
def reject(thread_id: str, body: RejectBody, request: Request):
    graph = request.app.state.graph
    try:
        graph.invoke(
            Command(resume={"approved": False, "note": body.note}),
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
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
