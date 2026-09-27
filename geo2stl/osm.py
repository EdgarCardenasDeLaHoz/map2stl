"""Overpass (OpenStreetMap) endpoints and osmnx configuration shared by every OSM fetcher.

City layers (``city2stl.fetch``) and trails (``geo2stl.trails``) both query
Overpass through osmnx; the mirror list, the health probe and the per-mirror
osmnx settings live here so the two cannot drift apart. ``overpass_query`` is
the raw-QL client (mirror rotation, pacing, backoff) for callers that need one
request per selector rather than osmnx (the align tools).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Overpass base URLs (osmnx appends /interpreter automatically).
# overpass-api.de is canonical but suffers frequent timeouts;
# kumi.systems and mail.ru are reliable mirrors.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
]

# The status probe's budget. Generous because a healthy mirror under load still
# takes ~10 s to answer /status — probing at 8 s rejected the only working
# mirror during an outage of the other two. Short enough that walking the whole
# list costs well under a minute when they are all down.
PROBE_TIMEOUT_S = 20.0

# Connecting is separate from the query budget: a mirror that does not accept
# the connection within this is down, and waiting the full query budget for it
# (seen: overpass-api.de stalling 300 s mid-fetch) only delays the fail-over.
CONNECT_TIMEOUT_S = 10


def healthy_overpass_endpoints() -> list[str]:
    """Overpass mirrors that answered ``/status``, in preference order.

    ``/status`` is the endpoint Overpass provides for this, and its status code
    is checked: a bare ``requests.head`` on the base URL does not raise on a 502,
    so a mirror that was up but broken used to be selected while a healthy one
    sat untried further down the list.

    The probe identifies itself the way the queries will (osmnx's user agent).
    overpass-api.de answers a bare ``python-requests`` user agent with 406, so
    the canonical instance was otherwise marked unhealthy on every fetch.
    """
    import requests

    try:
        import osmnx as ox
        headers = {"User-Agent": ox.settings.http_user_agent}
    except Exception:  # pragma: no cover - osmnx is a hard dependency of the callers
        headers = {}
    healthy: list[str] = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = requests.get(f"{endpoint}/status", headers=headers, timeout=PROBE_TIMEOUT_S)
            resp.raise_for_status()
            healthy.append(endpoint)
        except Exception as e:
            logger.warning(f"Overpass endpoint {endpoint} not healthy: {e}")
    return healthy


def use_overpass_endpoint(ox, endpoint: str, query_timeout_s: float) -> None:
    """Point osmnx at ``endpoint`` with bounded connect and query timeouts.

    osmnx's rate limiter polls ``{overpass_url}/status`` and sleeps until the
    server reports a free slot. Only the official instance publishes that in the
    format osmnx parses; against a third-party mirror the parse yields no slot
    and osmnx re-polls indefinitely (a ~56 s Cartagena fetch once sat for 55
    minutes). Rate-limit only the endpoint whose protocol osmnx speaks.

    requests takes ``(connect, read)``; osmnx also formats ``requests_timeout``
    into the query's ``[timeout:]`` clause, so that clause is fixed here.
    """
    ox.settings.overpass_url = endpoint
    ox.settings.overpass_rate_limit = endpoint.startswith("https://overpass-api.de")
    ox.settings.requests_timeout = (CONNECT_TIMEOUT_S, query_timeout_s)
    ox.settings.overpass_settings = f"[out:json][timeout:{int(query_timeout_s)}]{{maxsize}}"


# ---------------------------------------------------------------------------
# Raw Overpass QL (no osmnx)
# ---------------------------------------------------------------------------

#: Interpreter URLs for raw queries, one per mirror in :data:`OVERPASS_ENDPOINTS`.
OVERPASS_INTERPRETER_URLS = tuple(f"{e}/interpreter" for e in OVERPASS_ENDPOINTS)

_last_overpass_call = [0.0]


def overpass_wait(min_gap_s: float) -> None:
    """Hold back until *min_gap_s* has passed since the last raw Overpass request.

    The public endpoints hand out a few concurrent slots per client and answer
    429 once they are gone, so a caller firing many queries spaces them out.
    The clock is shared by every raw query in the process.
    """
    import time
    gap = min_gap_s - (time.monotonic() - _last_overpass_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_overpass_call[0] = time.monotonic()


def overpass_backoff(attempt: int, backoff_s: float) -> None:
    """Sleep after failed attempt *attempt*, doubling from *backoff_s* (capped at 8x)."""
    import time
    time.sleep(backoff_s * (2 ** min(attempt, 3)))
    _last_overpass_call[0] = time.monotonic()


def overpass_query(
    query: str,
    *,
    urls: tuple[str, ...] | list[str] = OVERPASS_INTERPRETER_URLS,
    attempts: int = 6,
    timeout_s: float = 180,
    min_gap_s: float = 0.0,
    backoff_s: float = 0.0,
    user_agent: str | None = None,
) -> list[dict]:
    """POST one Overpass QL *query* and return its ``elements``.

    Attempt *k* goes to ``urls[k % len(urls)]``, paced by :func:`overpass_wait`
    (*min_gap_s*) and, when *backoff_s* > 0, followed by :func:`overpass_backoff`
    on failure. The read timeout is *timeout_s* + 30 s (the query's own
    ``[timeout:]`` is the caller's). Raises the last error once every attempt
    has failed; 429 and 504 are routine on the public mirrors.
    """
    import requests

    headers = {"User-Agent": user_agent} if user_agent else {}
    attempts = max(1, int(attempts))
    last: Exception = RuntimeError("no attempt made")
    for attempt in range(attempts):
        url = urls[attempt % len(urls)]
        overpass_wait(min_gap_s)
        try:
            response = requests.post(url, data={"data": query},
                                     timeout=(20, timeout_s + 30), headers=headers)
            response.raise_for_status()
            return response.json().get("elements", [])
        except Exception as exc:  # noqa: BLE001 - retried on the next mirror
            last = exc
            if backoff_s > 0 and attempt + 1 < attempts:
                overpass_backoff(attempt, backoff_s)
    raise last
