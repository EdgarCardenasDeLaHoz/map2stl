"""Google Photorealistic 3D Tiles support: a pooled keep-alive HTTPS client, URI rewriting through /tiles3d,
and the on-disk tile cache."""
from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse

import paths  # noqa: F401  (puts the repo root on sys.path)

from geo2stl.cache import atomic_write

if TYPE_CHECKING:
    from regions import Config

TILES_HOST = "https://tile.googleapis.com"
TILES_PREFIX = "v1/3dtiles/"
TILES_CACHE_CAP = 2 * 1024 ** 3
TILES_CACHE_SWEEP_EVERY = 100   # writes between size checks
_idle: dict[str, list] = {}     # host -> idle keep-alive HTTPS connections, shared by handler threads
_idle_lock = threading.Lock()
IDLE_PER_HOST = 16


class _PooledResponse:
    """An http.client response that hands its connection back to the pool when closed after
    being read to the end (a half-read connection is closed instead)."""

    def __init__(self, resp, conn, host):
        self._resp, self._conn, self._host = resp, conn, host
        self.status, self.reason, self.headers = resp.status, resp.reason, resp.headers

    def read(self, *a):
        return self._resp.read(*a)

    def close(self):
        if self._conn is None:
            return
        done = self._resp.isclosed()
        self._resp.close()
        with _idle_lock:
            pool = _idle.setdefault(self._host, [])
            if done and len(pool) < IDLE_PER_HOST:
                pool.append(self._conn)
                self._conn = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def pooled_urlopen(req: urllib.request.Request, timeout: float = 30):
    """GET `req` over a pooled keep-alive connection, with urlopen's contract (HTTPError on >= 400).

    A fresh TLS handshake per tile cost ~440 ms against ~70 ms on a reused connection, and a
    view needs ~1,000 tile requests, so the offscreen render never settled. A stale pooled
    connection (closed by Google while idle) is dropped and the request retried on a new one.
    """
    import http.client
    u = urlparse(req.full_url)
    path = u.path + (f"?{u.query}" if u.query else "")
    for attempt in (0, 1):
        with _idle_lock:
            pool = _idle.get(u.netloc)
            conn = pool.pop() if pool and not attempt else None
        if conn is None:
            conn = http.client.HTTPSConnection(u.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers=dict(req.header_items()))
            resp = conn.getresponse()
        except (http.client.HTTPException, OSError):
            conn.close()
            if attempt:
                raise
            continue
        out = _PooledResponse(resp, conn, u.netloc)
        if resp.status >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason, resp.headers, out)
        return out


urlopen = pooled_urlopen  # tests replace this; the proxy never calls urllib directly


def rewrite_tile_uris(node, cfg: Config | None = None):
    """Point every child `uri`/`url` of a tileset JSON back through /tiles3d, in place.

    Google answers with absolute (`https://tile.googleapis.com/v1/...`) or root-relative
    (`/v1/...`) URIs; both become `/tiles3d/v1/...`. A `key` query parameter is dropped and the
    `session` token is remembered on `cfg` so tile requests that omit it can be completed.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("uri", "url") and isinstance(v, str):
                node[k] = _rewrite_uri(v, cfg)
            else:
                rewrite_tile_uris(v, cfg)
    elif isinstance(node, list):
        for v in node:
            rewrite_tile_uris(v, cfg)
    return node


def _rewrite_uri(uri: str, cfg: Config | None) -> str:
    if uri.startswith(TILES_HOST):
        uri = uri[len(TILES_HOST):]
    u = urlparse(uri)
    if cfg is not None:
        for k, v in parse_qsl(u.query):
            if k == "session" and v:
                cfg.session = v
    if u.query:
        q = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "key"])
        uri = u._replace(query=q).geturl()
    if uri.startswith("/v1/"):
        uri = "/tiles3d" + uri
    return uri


def tiles_upstream_url(cfg: Config, path: str, query: str) -> str | None:
    """Google URL for a proxied request, or None when the path is not under v1/3dtiles/."""
    rel = path.lstrip("/")
    if not rel.startswith(TILES_PREFIX) or ".." in rel.split("/") or "\\" in rel:
        return None
    q = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() != "key"]
    # root.json opens a new session and Google rejects one passed to it (400 "Unknown name
    # session"); only the child tiles it lists carry the token.
    if cfg.session and rel != TILES_PREFIX + "root.json" and not any(k == "session" for k, _ in q):
        q.append(("session", cfg.session))
    q.append(("key", cfg.api_key))
    return f"{TILES_HOST}/{rel}?{urlencode(q)}"


def _cache_paths(cache_dir: Path, rel: str) -> tuple[Path, Path]:
    import hashlib
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()
    d = Path(cache_dir) / h[:2]
    return d / (h + ".bin"), d / (h + ".json")


def tile_cache_get(cache_dir: Path, rel: str, now: float | None = None) -> tuple[bytes, str] | None:
    import time
    binp, metap = _cache_paths(cache_dir, rel)
    try:
        meta = json.loads(metap.read_text(encoding="utf-8"))
        if meta["expires"] <= (time.time() if now is None else now):
            return None
        return binp.read_bytes(), meta["ctype"]
    except (OSError, ValueError, KeyError):
        return None


def _max_age(cache_control: str | None) -> int:
    if not cache_control or "no-store" in cache_control.lower():
        return 0
    m = re.search(r"max-age\s*=\s*(\d+)", cache_control, re.I)
    return int(m.group(1)) if m else 0


_tile_writes = 0
_tile_lock = threading.Lock()


def tile_cache_put(cache_dir: Path, rel: str, body: bytes, ctype: str, max_age: int,
                   now: float | None = None) -> None:
    import time
    global _tile_writes
    binp, metap = _cache_paths(cache_dir, rel)
    binp.parent.mkdir(parents=True, exist_ok=True)
    exp = (time.time() if now is None else now) + max_age
    for p, data in ((binp, body), (metap, json.dumps({"ctype": ctype, "expires": exp}).encode())):
        atomic_write(p, lambda f, data=data: f.write(data))
    with _tile_lock:
        _tile_writes += 1
        sweep = _tile_writes % TILES_CACHE_SWEEP_EVERY == 0
    if sweep:
        tile_cache_trim(cache_dir)


def tile_cache_trim(cache_dir: Path, cap: int = TILES_CACHE_CAP) -> None:
    """Delete the oldest files until the cache is under `cap` bytes."""
    files = []
    for p in Path(cache_dir).glob("*/*"):
        try:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= cap:
            break
        try:
            p.unlink()
            total -= size
        except OSError:
            pass
