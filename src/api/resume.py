"""POST /api/approve, /api/reject, /api/organize, /api/undo, /api/dismiss."""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel

router = APIRouter()


def _check_checkpoint(graph, thread_id: str):
    """Raise HTTP 409 if no live checkpoint exists for this thread_id."""
    try:
        snap = graph.get_state({"configurable": {"thread_id": thread_id}})
        if not snap or not snap.values:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No active checkpoint for this file. "
                    "It may still be processing (refresh in a moment) "
                    "or it's a stale entry -- use Dismiss to clear it."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=409,
            detail="Could not verify checkpoint -- file may still be processing.",
        )


class ApproveBody(BaseModel):
    rename_to:   Optional[str] = None
    destination: Optional[str] = None
    note:        Optional[str] = None

class RejectBody(BaseModel):
    note: Optional[str] = None

class OrganizeBody(BaseModel):
    min_confidence: float = 0.85


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


@router.post("/dismiss-all")
def dismiss_all(request: Request):
    """Dismiss every pending item in the queue in one shot.

    Useful when a batch scan produced entries that can't be approved or rejected
    (e.g. files from a virtual/cloud drive like Google Drive).
    """
    conn = request.app.state.conn
    cur  = conn.execute(
        "UPDATE action_log SET status='dismissed' WHERE status='pending'"
    )
    conn.commit()
    count = cur.rowcount
    from src.events import publish
    publish({"type": "dismissed_all", "count": count})
    return {"ok": True, "dismissed": count}


@router.post("/dismiss-drive/{drive_letter}")
def dismiss_drive(drive_letter: str, request: Request):
    """Dismiss all pending items whose source path starts with <drive_letter>:.

    e.g. POST /api/dismiss-drive/G  clears all G: drive entries.
    """
    conn   = request.app.state.conn
    prefix = drive_letter.upper().rstrip(":") + ":\\"
    cur    = conn.execute(
        "UPDATE action_log SET status='dismissed' "
        "WHERE status='pending' AND UPPER(path) LIKE ?",
        (prefix.upper() + "%",),
    )
    conn.commit()
    count = cur.rowcount
    from src.events import publish
    publish({"type": "dismissed_all", "count": count, "drive": drive_letter.upper()})
    return {"ok": True, "dismissed": count, "drive": drive_letter.upper()}


@router.post("/organize")
def organize_all(body: OrganizeBody, request: Request):
    """Batch-approve every pending file whose AI confidence meets the threshold.

    Files below the threshold, or whose checkpoint is stale, are skipped.
    Returns: { organized: int, skipped: int, errors: int }
    """
    graph     = request.app.state.graph
    conn      = request.app.state.conn
    organized = 0
    skipped   = 0
    errors    = 0

    from src.db import get_pending_threads
    rows = get_pending_threads(conn)

    for row in rows:
        d        = dict(row)
        proposal = {}
        raw      = d.get("proposal")
        if raw:
            try:
                proposal = json.loads(raw)
            except Exception:
                pass

        confidence = float(proposal.get("confidence", 0.0))
        if confidence < body.min_confidence:
            skipped += 1
            continue

        thread_id = d["thread_id"]

        try:
            snap = graph.get_state({"configurable": {"thread_id": thread_id}})
            if not snap or not snap.values:
                skipped += 1
                continue
        except Exception:
            skipped += 1
            continue

        try:
            graph.invoke(
                Command(resume={"approved": True}),
                config={"configurable": {"thread_id": thread_id}},
            )
            organized += 1
        except Exception as exc:
            print(f"[organize] error approving {thread_id}: {exc}")
            errors += 1

    return {"organized": organized, "skipped": skipped, "errors": errors}


@router.post("/undo/{thread_id}")
def undo(thread_id: str, request: Request):
    conn = request.app.state.conn
    row  = conn.execute(
        "SELECT path, moved_to FROM action_log "
        "WHERE thread_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
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
