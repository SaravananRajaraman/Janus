"""GET /POST /api/rules — read and write rules.yaml."""
from __future__ import annotations
from pathlib import Path
import yaml
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

@router.get("/rules")
def get_rules(request: Request):
    path = Path(request.app.state.rules_path)
    with path.open() as f:
        return yaml.safe_load(f)

@router.post("/rules")
def update_rules(body: dict, request: Request):
    path = Path(request.app.state.rules_path)
    try:
        with path.open() as f:
            rules = yaml.safe_load(f)
        if "categories" in body:
            rules["categories"] = body["categories"]
        if "watch" in body:
            rules["watch"] = body["watch"]
        with path.open("w") as f:
            yaml.dump(rules, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "note": "Restart Janus to apply watch/category changes."}

@router.get("/activity")
def get_activity(request: Request, limit: int = 50):
    conn = request.app.state.conn
    import json
    rows = conn.execute(
        "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("proposal"):
            try: d["proposal"] = json.loads(d["proposal"])
            except Exception: pass
        result.append(d)
    return result
