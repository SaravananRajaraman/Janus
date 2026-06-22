"""LangGraph StateGraph -- Janus file-organiser pipeline.

Topology
--------
    extract -> analyze -> update_proposal -> dedupe
                                                |
                            .-------------------+--------------------.
                            v                   v                    v
                          dupes           auto_approve            approval (pause)
                       (duplicate)   (high-confidence +           (human)
                                      auto_organize.enabled)
                                              |                  .----+----.
                                              v                  v        v
                                            execute           execute   discard

auto_approve is only active when rules.yaml -> auto_organize.enabled = true
AND confidence >= min_confidence AND category not in skip_categories.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from langgraph.graph import END, StateGraph

from src.db import log_action, update_action
from src.events import publish
from src.extract import extract_content
from src.routing import make_route_after_dedupe, route_after_approval
from src.state import FileState


def make_graph(
    conn: sqlite3.Connection,
    *,
    chain: Any = None,
    categories: dict | None = None,
    drive_rules: dict | None = None,
    checkpointer: Any = None,
    dupes_path: str = ".organiser/.dupes",
    rejected_path: str = ".organiser/.rejected",
    dry_run: bool = False,
    auto_organize: dict | None = None,
):
    """Compile and return the Janus StateGraph."""

    # -- Node: extract --------------------------------------------------------
    def node_extract(state: FileState) -> dict:
        # Scan mode (skip_content=True): use filename + extension only.
        # This keeps drive scans fast -- no disk reads beyond stat().
        if state.get("skip_content"):
            content = None
        else:
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

    # -- Node: update_proposal ------------------------------------------------
    def node_update_proposal(state: FileState) -> dict:
        """Write AI analysis JSON to DB so the dashboard can display it."""
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

    # -- Node: auto_approve ---------------------------------------------------
    def node_auto_approve(state: FileState) -> dict:
        """Set approved=True without human intervention.

        Only reached when auto_organize.enabled=true and the confidence /
        category criteria pass in make_route_after_dedupe().
        """
        publish({
            "type":       "auto_approved",
            "thread_id":  state["thread_id"],
            "filename":   state["filename"],
            "confidence": state.get("confidence", 0.0),
        })
        print(
            f"[auto_approve] {state['filename']} "
            f"(confidence={state.get('confidence', 0):.0%}) -- auto-approved"
        )
        return {"approved": True}

    # -- Build remaining nodes ------------------------------------------------
    from src.nodes.dedupe   import make_dedupe_node
    from src.nodes.approval import node_approval
    from src.nodes.execute  import make_execute_node
    from src.nodes.dupes    import make_dupes_node
    from src.nodes.discard  import make_discard_node

    node_dedupe  = make_dedupe_node(conn)
    node_execute = make_execute_node(conn, dry_run=dry_run)
    node_dupes   = make_dupes_node(conn, dupes_path, dry_run=dry_run)
    node_discard = make_discard_node(conn, rejected_path=rejected_path, dry_run=dry_run)

    # Routing function (aware of auto_organize config)
    route_after_dedupe = make_route_after_dedupe(auto_organize)

    # -- Graph wiring ---------------------------------------------------------
    builder = StateGraph(FileState)
    builder.add_node("extract",         node_extract)
    builder.add_node("update_proposal", node_update_proposal)
    builder.add_node("dedupe",          node_dedupe)
    builder.add_node("auto_approve",    node_auto_approve)
    builder.add_node("approval",        node_approval)
    builder.add_node("execute",         node_execute)
    builder.add_node("dupes",           node_dupes)
    builder.add_node("discard",         node_discard)

    builder.set_entry_point("extract")

    if chain is not None:
        from src.nodes.analyze import make_analyze_node
        analyze = make_analyze_node(
            chain,
            categories=categories or {},
            drive_rules=drive_rules,
            dry_run=dry_run,
        )
        builder.add_node("analyze", analyze)
        builder.add_edge("extract",  "analyze")
        builder.add_edge("analyze",  "update_proposal")
    else:
        builder.add_edge("extract",  "update_proposal")

    builder.add_edge("update_proposal", "dedupe")

    builder.add_conditional_edges(
        "dedupe",
        route_after_dedupe,
        {"dupes": "dupes", "auto_approve": "auto_approve", "approval": "approval"},
    )

    # auto_approve feeds directly into execute (no interrupt)
    builder.add_edge("auto_approve", "execute")

    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {"execute": "execute", "discard": "discard"},
    )

    builder.add_edge("execute",  END)
    builder.add_edge("dupes",    END)
    builder.add_edge("discard",  END)

    return builder.compile(checkpointer=checkpointer)
