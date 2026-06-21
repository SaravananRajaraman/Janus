"""GET /api/feed — Server-Sent Events live activity stream."""
from __future__ import annotations
import asyncio, json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from src.events import _get_bus

router = APIRouter()

@router.get("/feed")
async def feed():
    bus = _get_bus()

    async def generate():
        yield ": connected\n\n"
        while True:
            try:
                event = bus.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                await asyncio.sleep(0.4)
                yield ": heartbeat\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
