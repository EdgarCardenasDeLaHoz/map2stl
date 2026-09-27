"""Overpass (OpenStreetMap) endpoints and osmnx configuration shared by every OSM fetcher.

City layers (``city2stl.fetch``) and trails (``geo2stl.trails``) both query
Overpass through osmnx; the mirror list, the health probe and the per-mirror
osmnx settings live here so the two cannot drift apart.
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
