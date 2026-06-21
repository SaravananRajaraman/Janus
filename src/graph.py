"""LangGraph StateGraph — Janus file-organiser pipeline.

Full Phase 3 topology:

    extract -> analyze -> dedupe
                              |-- (duplicate) --> dupes      [END]
                              |-- (unique)   --> approval    [INTERRUPT]
                                                    |-- (approved) --> execute  [END]
                                                    |-- (rejected) --> discard  [END]

The graph is compiled with a SqliteSaver checkpointer so the approval
interrupt survives process restarts (state is written to organiser.db).

Phase 4 adds: FastAPI routes that call graph.invoke(Command(resume=...)).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from langgraph.graph import END, StateGraph

from src.db import log_action, update_action
from src.extract import extract_content
from src.routing import route_after_approval, route_after_dedupe
from src.state import FileState


def make_graph(
    conn: sqlite3.Connection,
    *,
    chain: Any = None,
    categories: dict | None = None,
    checkpointer: Any = None,
    dupes_path: str = ".organiser/.dupes",
    dry_run: bool = False,
):
    """Compile and return the Janus StateGraph."""

    # ------------------------------------------------------------------
    # Node: extract — write initial "pending" row (no proposal yet)
    # ------------------------------------------------------------------
    def node_extract(state: FileState) -> dict:
        content = extract_content(state["path"])
        log_action(
            conn,
            thread_id=state["thread_id"],
            filename=state["filename"],
            path=state["path"],
            status="pending",
            proposal=None,
        )
        return {"content": content}

    # ------------------------------------------------------------------
    # Node: update_proposal — saves the AI proposal to the DB row so the
    # dashboard can display category / rename / destination / confidence.
    # Runs after analyze, before dedupe.
    # ------------------------------------------------------------------
    def node_update_proposal(state: FileState) -> dict:
        proposal = {
            "category":    state.get("category", ""),
            "intent":      state.get("intent", ""),
            "summary":     state.get("summary", ""),
            "rename_to":   state.get("rename_to", ""),
            "destination": state.get("destination", ""),
            "confidence":  state.get("confidence", 0.0),
        }
        conn.execute(
            "UPDATE action_log SET proposal = ? "
            "WHERE thread_id = ? AND id = (SELECT MAX(id) FROM action_log WHERE thread_id = ?)",
            (json.dumps(proposal), state["thread_id"], state["thread_id"]),
        )
        conn.commit()
        return {}

    # ------------------------------------------------------------------
    # Build node set
    # ------------------------------------------------------------------
    from src.nodes.dedupe   import make_dedupe_node
    from src.nodes.approval import node_approval
    from src.nodes.execute  import make_execute_node
    from src.nodes.dupes    import make_dupes_node
    from src.nodes.discard  import make_discard_node

    node_dedupe  = make_dedupe_node(conn)
    node_execute = make_execute_node(conn, dry_run=dry_run)
    node_dupes   = make_dupes_node(conn, dupes_path, dry_run=dry_run)
    node_discard = make_discard_node(conn)

    # ------------------------------------------------------------------
    # Graph wiring
    # ------------------------------------------------------------------
    builder = StateGraph(FileState)
    builder.add_node("extract",         node_extract)
    builder.add_node("update_proposal", node_update_proposal)
    builder.add_node("dedupe",          node_dedupe)
    builder.add_node("approval",        node_approval)
    builder.add_node("execute",         node_execute)
    builder.add_node("dupes",           node_dupes)
    builder.add_node("discard",         node_discard)

    builder.set_entry_point("extract")

    if chain is not None:
        from src.nodes.analyze import make_analyze_node
        analyze = make_analyze_node(chain, categories=categories or {}, dry_run=dry_run)
        builder.add_node("analyze", analyze)
        builder.add_edge("extract",         "analyze")
        builder.add_edge("analyze",         "update_proposal")
    else:
        builder.add_edge("extract",         "update_proposal")

    builder.add_edge("update_proposal", "dedupe")
    builder.add_conditional_edges("dedupe",   route_after_dedupe,   {"dupes": "dupes",     "approval": "approval"})
    builder.add_conditional_edges("approval", route_after_approval, {"execute": "execute", "discard":  "discard"})
    builder.add_edge("execute",  END)
    builder.add_edge("dupes",    END)
    builder.add_edge("discard",  END)

    return builder.compile(checkpointer=checkpointer)
