"""GET /api/scan/drives, POST /api/scan/start, GET /api/scan/status, POST /api/scan/stop."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


@router.get("/scan/drives")
def list_drives():
    """Return mounted non-system drives with capacity information.

    C: is excluded -- it is managed by the file watcher, not the scanner.
    Scanning C: risks moving OS/user files to wrong destinations.
    """
    from src.scanner import get_drives
    drives = [d for d in get_drives() if d["letter"].upper() != "C"]
    return {"drives": drives}


class ScanBody(BaseModel):
    drives: list[str]             # e.g. ["E:\\", "D:\\"]
    max_files: int | None = None  # override rules.yaml scan.max_files
    deep_scan: bool = False       # True = read file content; False = ext-only (faster)


@router.post("/scan/start")
def start_scan(body: ScanBody, request: Request):
    """Start a background drive scan.

    Files found are fed through the same classify + approval pipeline as the
    watcher.  Results appear in the Queue tab as they arrive.
    """
    scan_status = request.app.state.scan_status
    if scan_status.get("running"):
        raise HTTPException(status_code=409, detail="A scan is already running.")
    if not body.drives:
        raise HTTPException(status_code=422, detail="Select at least one drive.")

    graph            = request.app.state.graph
    db_path          = request.app.state.db_path
    scan_cfg         = request.app.state.scan_config
    skip_destinations = request.app.state.skip_destinations

    max_files = body.max_files or scan_cfg.get("max_files", 500)
    min_size  = scan_cfg.get("min_size_bytes", 1024)
    excl      = scan_cfg.get("exclude_paths", [])

    from src.scanner import start_background_scan
    start_background_scan(
        body.drives,
        graph,
        db_path,
        scan_status,
        exclude_paths=excl,
        skip_destinations=skip_destinations,
        max_files=max_files,
        min_size_bytes=min_size,
        deep_scan=body.deep_scan,
    )

    return {
        "ok":      True,
        "message": f"Scan started on {len(body.drives)} drive(s) — up to {max_files} files.",
    }


@router.get("/scan/status")
def get_scan_status(request: Request):
    """Return current scan progress."""
    return request.app.state.scan_status


@router.post("/scan/stop")
def stop_scan(request: Request):
    """Signal the background scan to stop after the current file."""
    request.app.state.scan_status["running"] = False
    return {"ok": True}
