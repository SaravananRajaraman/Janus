"""Thread-safe event bus for the SSE activity feed.

Nodes (execute, discard, dupes) call publish() from watchdog threads.
The SSE endpoint polls _bus from the asyncio event loop.

Kept deliberately simple: one shared queue, one consumer (the dashboard).
Multiple browser tabs will compete for events; that's acceptable for a
single-user tool. Per-subscriber fan-out can be added in a future pass.
"""
from __future__ import annotations

import queue
from typing import Any

_bus: queue.Queue[dict[str, Any]] = queue.Queue()


def publish(event: dict[str, Any]) -> None:
    """Push an event onto the bus (safe to call from any thread)."""
    _bus.put_nowait(event)


def _get_bus() -> queue.Queue:
    return _bus
