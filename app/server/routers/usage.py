"""usage.py — the local usage log (F-USAGE): what the user clicks, changes and waits for.

* ``POST /api/usage`` — ``{session, events: [...]}`` from ``static/js/modules/core/usage-log.js``;
  each event is appended as one JSON line to ``output/usage/<YYYY-MM-DD>.jsonl`` (local day),
  with ``session`` added. Nothing leaves the machine; ``output/`` is git-ignored.
* ``GET /api/usage/status`` — ``{enabled, dir, files: [{name, bytes}]}``.

The browser already skips password / key / token controls; the server drops any ``value``
whose control id, name or label mentions one as well, so a missed field cannot reach disk.
``MAP2STL_USAGE_DIR`` overrides the folder (tests). ``MAP2STL_USAGE_LOG=0`` turns the
endpoint into a no-op.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

MAX_EVENTS = 1000
MAX_BYTES = 512_000
_SECRET = re.compile(r"pass|key|token|secret|auth|credential", re.IGNORECASE)
_lock = threading.Lock()


def usage_dir() -> Path:
    env = os.environ.get("MAP2STL_USAGE_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "output" / "usage"


def enabled() -> bool:
    return os.environ.get("MAP2STL_USAGE_LOG", "1").strip().lower() not in ("0", "false", "off", "no")


def _scrub(ev: dict) -> dict:
    who = " ".join(str(ev.get(k) or "") for k in ("id", "name", "label"))
    if "value" in ev and _SECRET.search(who):
        ev = {**ev, "value": "[redacted]"}
    return ev


@router.post("/api/usage")
async def post_usage(request: Request):
    if not enabled():
        return {"written": 0, "enabled": False}
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "usage batch too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(400, "invalid JSON") from exc
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise HTTPException(400, f"events must be a list of at most {MAX_EVENTS}")
    session = str(data.get("session") or "")[:64]
    lines = [json.dumps({"session": session, **_scrub(ev)}, ensure_ascii=False)
             for ev in events if isinstance(ev, dict)]
    if lines:
        d = usage_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{datetime.now():%Y-%m-%d}.jsonl"
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return {"written": len(lines), "enabled": True}


@router.get("/api/usage/status")
def usage_status():
    d = usage_dir()
    files = sorted(d.glob("*.jsonl")) if d.is_dir() else []
    return {"enabled": enabled(), "dir": str(d),
            "files": [{"name": f.name, "bytes": f.stat().st_size} for f in files]}
