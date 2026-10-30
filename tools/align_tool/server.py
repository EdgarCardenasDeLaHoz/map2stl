"""Local backend for the manual drag-align tool (drag_align.html).

Serves the align tool directory as static files and adds four JSON endpoints
that a plain file:// page cannot provide:

    POST /api/save     write the current alignment to ground_truth/<slug>.json
    POST /api/load     read that file back when the city is selected again
    POST /api/refine   run an ECC registration refine seeded with the user's transform
    POST /api/refetch  re-fetch OSM buildings / water / satellite for a corrected bbox

The third one is the important one. The pipeline derives each city's OSM window
from `tallest_m / (z_max - z_min)` -- a Z *range* that includes terrain relief and
the base plate, not the tallest building -- and centres it on the centroid of the
geocoded administrative bounds. For hilly or oddly-bounded cities the resulting
window is the wrong size and/or the wrong place, so the true match can lie outside
the fetched raster entirely and no amount of dragging will find it. /api/refetch
lets the tool ask for the window the user actually wants.

Run with the working venv:
    ~/.venvs/map2stl/Scripts/python.exe tools/align_tool/server.py [--port 8766]
Then open http://localhost:8766/drag_align.html
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import paths  # noqa: E402

HERE = paths.HERE

GROUND_TRUTH = paths.GROUND_TRUTH
MAX_BODY = 8 * 1024 * 1024


from city2stl.registration.align_edit import (  # noqa: E402,F401
    _cell_size_m,
    _fetch_sat,
    _load_gray,
    _meta,
    _norm_u8,
    _png_data_uri,
    _square_bbox,
    _stl_footprint_bbox,
    api_load,
    api_refetch,
    api_refine,
    api_save,
)

ROUTES = {
    "/api/save": api_save,
    "/api/load": api_load,
    "/api/refine": api_refine,
    "/api/refetch": api_refetch,
}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class AlignHandler(SimpleHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/api/health":
            self._json(200, {"ok": True, "server": "align", "root": str(HERE)})
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        route = ROUTES.get(self.path.split("?", 1)[0].rstrip("/"))
        if route is None:
            self._json(404, {"ok": False, "error": f"no such endpoint: {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError(f"request body too large ({length} bytes)")
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(200, route(body))
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    handler = partial(AlignHandler, directory=str(HERE))
    srv = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"align server on http://{args.host}:{args.port}/drag_align.html", flush=True)
    print(f"  static root : {HERE}", flush=True)
    print(f"  ground truth: {GROUND_TRUTH}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
