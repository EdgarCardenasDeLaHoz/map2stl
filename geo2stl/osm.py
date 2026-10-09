"""Overpass (OpenStreetMap) endpoints and osmnx configuration shared by every OSM fetcher.

City layers (``city2stl.fetch``) and trails (``geo2stl.trails``) both query
Overpass through osmnx; the mirror list, the health probe and the per-mirror
osmnx settings live here so the two cannot drift apart. ``overpass_query`` is
the raw-QL client (mirror rotation, pacing, backoff) for callers that need one
request per selector rather than osmnx (the align tools).

``install_dns_pin`` replaces osmnx's IPv4-only host pin (``osmnx._http._config_dns``)
with ``pin_overpass_host``, which pins each Overpass host to the first of its
addresses, IPv6 or IPv4, that accepts a connection. ``use_overpass_endpoint`` and
``city2stl.osm_raster`` install it before any osmnx query.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from urllib.parse import urlparse

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

    def probe(endpoint: str) -> bool:
        try:
            resp = requests.get(f"{endpoint}/status", headers=headers, timeout=PROBE_TIMEOUT_S)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Overpass endpoint %s not healthy: %s", endpoint, type(e).__name__)
            return False

    # Probes run in parallel and are remembered for PROBE_MEMORY_S: one slow mirror cost
    # a 10 s timeout per fetch, and a city load fetches buildings, lakes, railways and
    # green within seconds of each other (Miami, 2026-10-03: six probes in 70 s).
    now = time.monotonic()
    with _PROBE_LOCK:
        stale = [e for e in OVERPASS_ENDPOINTS
                 if now - _PROBED.get(e, (-1e9, False))[0] >= PROBE_MEMORY_S]
        if stale:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(len(stale)) as pool:
                for e, ok in zip(stale, pool.map(probe, stale), strict=True):
                    _PROBED[e] = (time.monotonic(), ok)
        up = [e for e in OVERPASS_ENDPOINTS if _PROBED[e][1]]
    healthy: list[str] = []
    recently_failed: list[str] = []
    for endpoint in up:
        if now - _FAILED_AT.get(endpoint, -1e9) < FAILURE_MEMORY_S:
            recently_failed.append(endpoint)
        else:
            healthy.append(endpoint)
    # A mirror can answer /status yet time out on real queries (overpass-api.de, 2026-10-02:
    # ~100 s per request before the next mirror was tried). One that failed a real query in the
    # last FAILURE_MEMORY_S goes last, so it is only tried when nothing else is up.
    return healthy + recently_failed


#: How long a mirror that failed a real query is tried last.
FAILURE_MEMORY_S = 600.0
#: How long a /status probe result is reused.
PROBE_MEMORY_S = 120.0
_PROBED: dict[str, tuple[float, bool]] = {}
_PROBE_LOCK = threading.Lock()
_FAILED_AT: dict[str, float] = {}


def mark_overpass_failure(endpoint: str) -> None:
    """Remember that *endpoint* failed a real query (see :func:`healthy_overpass_endpoints`).

    Its host's address pin is dropped too, so the next query to it re-checks which address
    still connects.
    """
    _FAILED_AT[endpoint] = time.monotonic()
    _PINS.pop(urlparse(endpoint).hostname or "", None)


# ---------------------------------------------------------------------------
# Host pin (osmnx)
# ---------------------------------------------------------------------------
# osmnx 2.x pins the Overpass host before every query (``osmnx._http._config_dns``): it resolves
# the name with ``socket.gethostbyname``, which is IPv4 only, and patches ``socket.getaddrinfo``
# process-wide so every later connection to that host, the /status probe included, goes to that
# one IPv4 address. The point is to keep the /status slot check and the query on the same server
# of a round-robin. From a network where IPv4 to overpass-api.de is down and IPv6 works (this
# PC, 2026-10-08: ``curl -4`` cannot connect, ``curl -6`` answers in 0.3 s) every osmnx query
# then ended in ConnectTimeout. ``pin_overpass_host`` keeps the one-server pin but picks the
# first of the host's addresses, in the resolver's order (IPv6 first where the OS prefers it),
# that accepts a TCP connection, and leaves the host unpinned when none does.

#: TCP connect budget per address when choosing the address to pin.
PIN_CONNECT_TIMEOUT_S = 3.0
#: How long a chosen address is reused before the host is checked again.
PIN_MEMORY_S = 600.0
#: hostname -> (monotonic time chosen, pinned IP or None for "unpinned").
_PINS: dict[str, tuple[float, str | None]] = {}
_PIN_LOCK = threading.Lock()
#: The resolver the pin delegates to (osmnx's saved original once :func:`install_dns_pin` ran).
_original_getaddrinfo = socket.getaddrinfo


def _tcp_connects(sockaddr, family: int, timeout_s: float) -> bool:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(sockaddr)
        return True
    except OSError:
        return False


def reachable_address(hostname: str, port: int = 443,
                      timeout_s: float = PIN_CONNECT_TIMEOUT_S) -> str | None:
    """The first of *hostname*'s addresses (resolver order) that accepts a TCP connection."""
    try:
        infos = _original_getaddrinfo(hostname, port, 0, socket.SOCK_STREAM)
    except OSError:
        return None
    tried: set[str] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0]
        if ip in tried:
            continue
        tried.add(ip)
        if _tcp_connects(sockaddr, family, timeout_s):
            return ip
        logger.warning("Overpass host %s: %s did not accept a connection in %.0f s",
                       hostname, ip, timeout_s)
    return None


def pin_overpass_host(url: str) -> None:
    """Drop-in for ``osmnx._http._config_dns``: pin *url*'s host to an address that connects.

    The choice is remembered for ``PIN_MEMORY_S`` (and dropped by
    :func:`mark_overpass_failure`); with no address connecting the host stays unpinned, so
    requests resolves it as usual.
    """
    parts = urlparse(url)
    host = parts.hostname
    if not host:
        return
    port = parts.port or (80 if parts.scheme == "http" else 443)
    with _PIN_LOCK:
        hit = _PINS.get(host)
        if hit is not None and time.monotonic() - hit[0] < PIN_MEMORY_S:
            return
        ip = reachable_address(host, port)
        _PINS[host] = (time.monotonic(), ip)
    logger.info("Overpass host %s pinned to %s", host, ip or "nothing (unpinned)")


def _pinned_getaddrinfo(*args, **kwargs):
    """``socket.getaddrinfo`` with pinned hosts swapped for their address.

    Falls back to the host name when the pinned address does not fit the call (an IPv6
    pin asked for ``AF_INET`` only).
    """
    host = args[0] if args else kwargs.get("host")
    pin = _PINS.get(host) if isinstance(host, str) else None
    if pin is not None and pin[1]:
        if args:
            pinned_args, pinned_kwargs = (pin[1], *args[1:]), kwargs
        else:
            pinned_args, pinned_kwargs = args, {**kwargs, "host": pin[1]}
        try:
            return _original_getaddrinfo(*pinned_args, **pinned_kwargs)
        except socket.gaierror:
            pass
    return _original_getaddrinfo(*args, **kwargs)


def install_dns_pin(ox=None) -> None:
    """Make osmnx pin hosts with :func:`pin_overpass_host` instead of its IPv4-only pin.

    Idempotent. *ox* is the osmnx module (imported when omitted); a stand-in without
    ``_http`` (tests) is left alone. Undoes an osmnx pin already in place.
    """
    global _original_getaddrinfo
    if ox is None:
        try:
            import osmnx as ox
        except ImportError:  # pragma: no cover - osmnx is a hard dependency of the callers
            return
    http = getattr(ox, "_http", None)
    if http is None or not hasattr(http, "_config_dns"):
        return
    with _PIN_LOCK:
        if http._config_dns is pin_overpass_host and socket.getaddrinfo is _pinned_getaddrinfo:
            return
        resolver = getattr(http, "_original_getaddrinfo", socket.getaddrinfo)
        if resolver is not _pinned_getaddrinfo:  # never delegate to ourselves
            _original_getaddrinfo = resolver
        http._config_dns = pin_overpass_host
        socket.getaddrinfo = _pinned_getaddrinfo


def use_overpass_endpoint(ox, endpoint: str, query_timeout_s: float) -> None:
    """Point osmnx at ``endpoint`` with bounded connect and query timeouts.

    osmnx's rate limiter polls ``{overpass_url}/status`` and sleeps until the
    server reports a free slot. Only the official instance publishes that in the
    format osmnx parses; against a third-party mirror the parse yields no slot
    and osmnx re-polls indefinitely (a ~56 s Cartagena fetch once sat for 55
    minutes). Rate-limit only the endpoint whose protocol osmnx speaks.

    requests takes ``(connect, read)``; osmnx also formats ``requests_timeout``
    into the query's ``[timeout:]`` clause, so that clause is fixed here.

    Also swaps osmnx's IPv4-only host pin for :func:`pin_overpass_host`
    (:func:`install_dns_pin`).
    """
    install_dns_pin(ox)
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
