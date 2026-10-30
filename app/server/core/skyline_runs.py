"""core/skyline_runs.py — the Street View heights page (F-UNIFY Phase 4): the reports of a
region, the map of one report, the cost estimate of a run, and the run itself.

* **Reports**: every folder under the region-reports root whose name starts with the region
  (``Cartagena_v20_agree``, ``miami_survey_skyline_report``) and holds a ``heights.json``.
  The header (tier counts, building count) is cached by file mtime: a Miami file is 30 MB.
* **Estimate** (``estimate``): no network. A panorama is a seed of ``sites/<region>.json``
  (or, without any, the top saved proposals) or an auto proposal; when the region has a
  report, the panoramas that report used set a floor (``report_panoramas``), the unverifiable
  ones counted as paid, so the confirm never understates. Without a report it is a rough
  lower bound (``rough``). A panorama takes 12 spin images; an image is cached when the image cache (or its "no imagery"
  marker) answers it. Paid fetches = images not cached. It leaves out the pitch-corrected
  re-fetch of a panorama and the screening thumbnails, and is unknown (``known=False``) for a
  region with no seeds yet (the run proposes them from OSM).
* **Run** (``run_region``): ``city2stl.skyline.region_pdf.run_region_pdf_report`` in a job,
  inside ``google_usage.cache_only()`` unless the caller allowed paid fetches, so nothing
  Google bills can be sent without the flag. Cancel stops at the next step or panorama.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from app.server.core.jobs import Job
from geo2stl import google_usage

PANOS_STEP_DEG = 30.0          # the pipeline's spin step (orchestrator.spin_step_deg)
WARN_SHARE = 0.8               # confirm in red above this share of the monthly cap
STEPS = ("Reading the map", "Screening viewpoints", "Detecting rooflines", "Writing heights")
_VERSION = re.compile(r"(?:^|_)v(\d+)(?:_|$)", re.IGNORECASE)
_SUFFIX = "_skyline_report"
_header_cache: dict[str, tuple[float, dict]] = {}
_header_lock = threading.Lock()


@dataclass
class RunJob(Job):
    """A heights run: the common job state plus the step the page shows."""
    step: int = 0                  # 1-based index into STEPS, 0 before the first
    done: int | None = None        # panoramas finished in the detection step
    total: int | None = None
    report: str | None = None      # name of the report folder being written

    def snapshot(self) -> dict:
        return {**super().snapshot(), "report": self.report, "step": self.step,
                "n_steps": len(STEPS), "step_label": STEPS[self.step - 1] if self.step else None,
                "done": self.done, "total": self.total,
                "gpu": gpu_state(self.alive(), self.step)}


class RunCancelled(Exception):
    """Raised from the progress callback to stop a run at a step boundary."""


def reports_root() -> Path:
    from app.server.routers import reports

    return reports._ROOTS["region"]


def region_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------------------------------------------------------------------- reports


def _header(heights: Path) -> dict | None:
    """Tier counts and building count of a ``heights.json`` (cached by mtime)."""
    try:
        mtime = heights.stat().st_mtime
    except OSError:
        return None
    key = str(heights)
    with _header_lock:
        hit = _header_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(heights.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    from city2stl.skyline.tier_display import tier_counts

    buildings = data.get("buildings") or []
    head = {"n_buildings": len(buildings),
            "tier_counts": tier_counts(buildings, data.get("tier_counts")),
            "bbox_nsew": data.get("bbox_nsew"), "region": data.get("region")}
    with _header_lock:
        _header_cache[key] = (mtime, head)
    return head


def _title(dir_name: str, key: str) -> str:
    rest = dir_name[len(key):] if dir_name.lower().startswith(key) else dir_name
    rest = rest.removesuffix(_SUFFIX).strip("_ ")
    return rest.replace("_", " ") or "default settings"


def reports_for(region: str) -> list[dict]:
    """The region's reports, newest first (no building lists; see :func:`map_payload`)."""
    root, key = reports_root(), region_key(region)
    out: list[dict] = []
    if not root.is_dir() or not key:
        return out
    for d in root.iterdir():
        name = d.name.lower()
        if not d.is_dir() or not (name == key or name.startswith(key + "_")):
            continue
        heights = d / "heights.json"
        if not heights.is_file():
            continue
        head = _header(heights)
        if head is None:
            continue
        m = _VERSION.search(d.name)
        mtime = heights.stat().st_mtime
        out.append({
            "dir": d.name, "title": _title(d.name, key),
            "version": int(m.group(1)) if m else None,
            "modified": time.strftime("%Y-%m-%d", time.localtime(mtime)),
            "mtime": mtime,
            "n_buildings": head["n_buildings"], "tier_counts": head["tier_counts"],
            "index_url": (f"/reports/files/region/{d.name}/index.html"
                          if (d / "index.html").is_file() else None),
        })
    out.sort(key=lambda r: (r["version"] or 0, r["mtime"]), reverse=True)   # newest version first
    return out


def next_report_name(region: str) -> str:
    """``<Region>_v<N+1>_skyline_report``: N is the highest version among the region's reports."""
    vs = [r["version"] for r in reports_for(region) if r["version"]]
    safe = re.sub(r"[^A-Za-z0-9]+", "_", region).strip("_")
    return f"{safe}_v{max(vs, default=0) + 1}{_SUFFIX}"


def map_payload(region_dir: str) -> dict | None:
    """Footprints of one report for the page's map: compact rows, coordinates to 5 decimals."""
    from city2stl.skyline.tier_display import TIER_COLORS, TIER_LABELS, row_tier

    if region_dir != Path(region_dir).name:
        return None
    heights = reports_root() / region_dir / "heights.json"
    if not heights.is_file():
        return None
    try:
        data = json.loads(heights.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = []
    for b in data.get("buildings") or []:
        ring = b.get("footprint_lonlat") or []
        if len(ring) < 3:
            continue
        rows.append({
            "id": b.get("feature_id"), "name": b.get("name"),
            "h": b.get("effective_height_m"), "tier": row_tier(b),
            "src": b.get("effective_height_source"),
            "panos": b.get("n_seeds") or 0, "tag": b.get("height_tag_m"),
            "ring": [[round(x, 5), round(y, 5)] for x, y in ring],
        })
    head = _header(heights) or {}
    return {"dir": region_dir, "bbox_nsew": data.get("bbox_nsew"),
            "tier_counts": head.get("tier_counts"),
            "tiers": {t: {"label": TIER_LABELS[t], "color": TIER_COLORS[t]} for t in TIER_LABELS},
            "buildings": rows}


# --------------------------------------------------------------------------- estimate


def _spin_params(heading, pitch, fov, size, *, pano_id, lat, lon) -> dict:
    """The params ``streetview_io._streetview_image`` caches an image under (key excluded).
    ``tests/test_skyline_runs.py`` pins this to what that function really sends."""
    base = {"heading": heading, "pitch": pitch, "fov": fov, "size": size}
    if pano_id:
        return {**base, "pano": pano_id}
    return {**base, "location": f"{lat},{lon}", "radius": 200, "source": "outdoor"}


def seed_points(region: str) -> list[dict]:
    """The panoramas a run would use: ``{lat, lon, pitch, fov, pano_id}`` per seed."""
    from city2stl.skyline import seed_selection
    from city2stl.skyline.region_data import _load_site_seed_urls
    from city2stl.skyline.streetview_io import _parse_streetview_url

    resolved: dict = {}
    try:
        resolved = json.loads((Path(seed_selection.__file__).parent / "_pano" / "runs"
                               / "seed_resolution_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    pts = []
    for i, url in enumerate(_load_site_seed_urls(region)):
        parsed = _parse_streetview_url(url)
        if parsed is None:
            continue
        lat, lon, _heading, fov, pitch, pano_id = parsed
        hit = resolved.get(f"seed_{i + 1}|{lat:.6f}|{lon:.6f}|{pano_id or ''}")
        if isinstance(hit, dict) and "lat" in hit and "lon" in hit:
            lat, lon, pano_id = float(hit["lat"]), float(hit["lon"]), hit.get("pano_id") or None
        pts.append({"lat": lat, "lon": lon, "pitch": pitch, "fov": fov, "pano_id": pano_id})
    if not pts:      # a region with no seed URLs runs on its top 3 saved proposals
        try:
            doc = json.loads((seed_selection._PROPOSALS_DIR / f"{region.lower()}.json")
                             .read_text(encoding="utf-8"))
            rows = doc if isinstance(doc, list) else doc.get("points", [])
        except (OSError, ValueError):
            rows = []
        pts = [{"lat": r["lat"], "lon": r["lon"], "pitch": r.get("pitch", 0.0),
                "fov": r.get("fov", 80.0), "pano_id": r.get("pano_id")} for r in rows[:3]]
    return pts


def auto_points(region: str) -> list[dict]:
    """The auto-proposed viewpoints a run screens and may use (``runs/auto_proposals``)."""
    from city2stl.skyline import seed_selection

    try:
        doc = json.loads((seed_selection._PROPOSALS_DIR / f"{region.lower()}.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc if isinstance(doc, list) else doc.get("points", [])
    return [{"lat": r["lat"], "lon": r["lon"], "pitch": r.get("pitch", 0.0),
             "fov": r.get("fov", 80.0), "pano_id": r.get("pano_id")}
            for r in rows if "lat" in r and "lon" in r]


_seed_cache: dict[str, tuple[float, int]] = {}


def report_panoramas(region: str) -> tuple[int, str | None]:
    """Panoramas the newest report of ``region`` measured from: its distinct seeds, read from
    the per-building readings. ``(0, None)`` without a report. Cached by file mtime."""
    for rep in reports_for(region):
        heights = reports_root() / rep["dir"] / "heights.json"
        try:
            mtime = heights.stat().st_mtime
            with _header_lock:
                hit = _seed_cache.get(str(heights))
            if hit and hit[0] == mtime:
                n = hit[1]
            else:
                doc = json.loads(heights.read_text(encoding="utf-8"))
                names: set[str] = set()
                for b in doc.get("buildings", []):
                    names.update((b.get("per_seed_median_m") or {}).keys())
                    for v in b.get("views") or []:
                        names.add(str(v.get("view_name", "")).rsplit("_", 1)[0])
                names.discard("")
                n = len(names)
                with _header_lock:
                    _seed_cache[str(heights)] = (mtime, n)
        except (OSError, ValueError):
            continue
        if n:
            return n, rep["dir"]
    return 0, None


def estimate(region: str) -> dict:
    """Cost of a run before it starts. See the module doc for what is (not) counted."""
    from city2stl.skyline import streetview_io as sv

    w, h = sv._default_streetview_image_size()
    size = f"{w}x{h}"
    headings = [float(x) for x in range(0, 360, int(PANOS_STEP_DEG))]
    seen: set[str] = set()
    seen_at: set[tuple[float, float]] = set()
    panos = cached_panos = images = cached_images = 0
    seeds = seed_points(region)
    for p in seeds + auto_points(region):
        at = (round(p["lat"], 6), round(p["lon"], 6))
        if at in seen_at:
            continue
        seen_at.add(at)
        if p["pano_id"]:
            if p["pano_id"] in seen:
                continue
            seen.add(p["pano_id"])
        n_cached = sum(
            sv.image_is_cached(_spin_params(hd, p["pitch"], p["fov"], size, pano_id=p["pano_id"],
                                            lat=p["lat"], lon=p["lon"])) for hd in headings)
        panos += 1
        images += len(headings)
        cached_images += n_cached
        cached_panos += n_cached == len(headings)
    # A previous report is the best guide to how many panoramas a run really uses. Any it
    # used beyond the viewpoints checked above cannot be looked up in the cache, so they count
    # as paid: the confirm may overstate, never understate.
    prev_n, prev_dir = report_panoramas(region)
    unverified = max(0, prev_n - panos)
    panos += unverified
    images += unverified * len(headings)
    usage = google_usage.month_counts()["streetview_image"]
    paid = images - cached_images
    basis = "previous_report" if prev_n else "seeds"
    if not panos:
        note = "This region has no saved viewpoints yet; the run chooses them from the map."
    elif basis == "previous_report":
        note = (f"Based on the viewpoints and the {prev_n} panoramas of {prev_dir}. "
                "Images that cannot be looked up are counted as paid, so this is an upper "
                "bound if they are already cached.")
    else:
        note = ("Rough lower bound: counts the 12 spin images of each saved viewpoint; a run "
                "may fetch more (corrected pitch, screening).")
    return {
        "region": region, "known": panos > 0,
        "panoramas": panos, "cached_panoramas": cached_panos,
        "images": images, "cached_images": cached_images, "paid_fetches": paid,
        "basis": basis, "rough": basis == "seeds", "basis_report": prev_dir,
        "month": google_usage.current_month(),
        "month_count": usage["count"], "cap": usage["cap"],
        "share_of_cap": round(paid / usage["cap"], 4),
        "over_warn": usage["count"] + paid > WARN_SHARE * usage["cap"],
        "note": note,
    }


# ------------------------------------------------------------------------------- run

_STEP_OF = {"OSM fetch": 1, "Screen locations (Street View)": 2, "Auto-replace bad seeds": 2,
            "Multiview registration (per-seed)": 3, "Writing heights": 4}


def gpu_state(running: bool, step: int) -> str:
    """'in use' while this process holds the machine GPU lock; 'waiting' during detection
    without it (another process has the card, or this run freed it between panoramas)."""
    from city2stl import resources

    if not running:
        return "idle"
    if resources._gpu_lock_fd is not None:
        return "in use"
    return "waiting" if step == 3 else "idle"


def run_region(job, region: str, name: str, *, allow_paid: bool, runner=None) -> dict:
    """Run the region's pipeline into ``<reports root>/<name>/``; progress goes to ``job``.

    ``runner`` replaces ``run_region_pdf_report`` (tests). Returns the job result; a cancel
    ends the job as cancelled and returns ``{}``.
    """
    if runner is None:
        from city2stl.skyline.region_pdf import run_region_pdf_report as runner

    def cb(label, done, total):
        if job.cancel_event.is_set():
            raise RunCancelled
        step = _STEP_OF.get(label)
        if step:
            job.step = step
        step = job.step
        if done is not None and total:
            msg = f"{done} / {total} panoramas"
            pct = 40 + int(50 * done / total)
            job.done, job.total = done, total
        else:
            msg = STEPS[step - 1] if step else "Starting"
            pct = {0: 2, 1: 8, 2: 20, 3: 40, 4: 92}.get(step, 2)
        job.update(pct, msg)

    out_pdf = reports_root() / f"{name}.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (nullcontext({}) if allow_paid else google_usage.cache_only()) as skipped:
            summary = runner(region, out_pdf, skip_pdf=True, progress_cb=cb)
    except RunCancelled:
        job.finish("cancelled", "Cancelled")
        return {}
    return {"report": name, "summary": summary,
            "skipped_paid": dict(skipped)}
