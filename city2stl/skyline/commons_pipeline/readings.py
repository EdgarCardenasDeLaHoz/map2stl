"""Photos that read the same tower must agree; per-building rows from the kept readings."""

from __future__ import annotations

import numpy as np

from city2stl.skyline import photo_heights as ph

#: Two photos confirm each other on a tower when their readings lie within max(CROSS_ABS_M,
#: CROSS_REL x the other photos' median).
CROSS_ABS_M = 5.0
CROSS_REL = 0.15


def _cross_check(results: list[dict]) -> None:
    """Photos that claim the same tower must agree on it (user, 2026-10-05).

    Per used reading of a kept photo: ``others`` = how many other kept photos read that tower,
    ``confirmed_by`` = how many of them agree with it. A reading that two or more other photos
    read and none agrees with is flagged "other photos disagree" and not used. On 2,472
    readings with confirmed truth (Miami, Chicago): confirmed readings were more than 25 % off
    in 11 % of cases, contradicted ones in 62 %, readings no other photo had in 32 %.
    """
    by: dict[int, list[tuple[int, dict]]] = {}
    for k, r in enumerate(results):
        if r.get("kept"):
            for t in r.get("towers") or []:
                if not t.get("flag"):
                    by.setdefault(t["tower"], []).append((k, t))
    for lst in by.values():
        for k, t in lst:
            others = [o["photo_m"] for k2, o in lst if k2 != k]
            t["others"] = len(others)
            if others:
                tol = max(CROSS_ABS_M, CROSS_REL * float(np.median(others)))
                t["confirmed_by"] = int(sum(abs(h - t["photo_m"]) <= tol for h in others))
    for lst in by.values():
        for _k, t in lst:
            if t["others"] >= 2 and not t.get("confirmed_by"):
                t["flag"] = "other photos disagree"


def _photo_groups(results: list[dict], min_shared: int = 4) -> list[dict]:
    """Kept photos grouped by the towers they show: linked when two share ``min_shared`` used
    tower readings, groups = connected sets. Per group: its photos, the towers read by 2+ of
    them, and the share of those readings another photo confirms."""
    kept = [r for r in results if r.get("kept")]
    sets = [{t["tower"] for t in r.get("towers") or [] if not t.get("flag")} for r in kept]
    parent = list(range(len(kept)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            if len(sets[i] & sets[j]) >= min_shared:
                parent[find(i)] = find(j)
    members: dict[int, list[int]] = {}
    for i in range(len(kept)):
        members.setdefault(find(i), []).append(i)
    out = []
    for ms in members.values():
        shared = {ti for ti in set().union(*(sets[i] for i in ms)) if sum(ti in sets[i] for i in ms) >= 2}
        reads = [t for i in ms for t in kept[i]["towers"]
                 if t["tower"] in shared and t.get("others") and not t.get("flag")]
        flagged = [t for i in ms for t in kept[i]["towers"]
                   if t["tower"] in shared and t.get("flag") == "other photos disagree"]
        out.append({"photos": [kept[i]["key"] for i in ms], "titles": [kept[i]["title"] for i in ms],
                    "towers": len(shared), "readings": len(reads) + len(flagged),
                    "confirmed": sum(1 for t in reads if t.get("confirmed_by")),
                    "contradicted": len(flagged)})
    return sorted(out, key=lambda g: (-len(g["photos"]), -g["towers"]))


def _shared_buildings(results: list[dict], rows: list[dict]) -> list[dict]:
    """Every tower two or more kept photos read: each photo's reading (and card label), the
    consensus and truth, most-seen first."""
    by: dict[int, list[dict]] = {}
    for r in results:
        if r.get("kept"):
            for t in r.get("towers") or []:
                by.setdefault(t["tower"], []).append(
                    {"photo": r["key"], "title": r["title"], "label": t.get("label"),
                     "photo_m": t["photo_m"], "flag": t.get("flag"),
                     "confirmed_by": t.get("confirmed_by", 0)})
    row = {r["tower"]: r for r in rows}
    out = []
    for ti, reads in by.items():
        if len(reads) < 2:
            continue
        b = row.get(ti, {})
        out.append({"tower": ti, "name": b.get("name") or "", "reads": reads,
                    "photo_m": b.get("photo_m"), "osm_m": b.get("osm_m"), "truth_m": b.get("truth_m"),
                    "confirmed": sum(1 for x in reads if x["confirmed_by"] and not x["flag"])})
    return sorted(out, key=lambda b: (-len(b["reads"]), b["name"]))


def _building_row(towers, keys: dict, ti: int, reads: list[tuple], weighted: bool,
                  use_peak: bool, n_confirmed: int, truth_m) -> dict:
    """One building's row from its readings ``(median m, peak m, photo anchor dev)``: the top
    (``ph.choose_top``), then the combination (``ph.combine_photo_heights``). ``photo_median_m``
    is the plain median of the median readings, what this stage gave before T17/T30."""
    med = [r[0] for r in reads]
    top = ph.choose_top(med, [r[1] for r in reads]) if use_peak else "median"
    hs = [r[1] for r in reads] if top == "peak" else med
    devs = [r[2] for r in reads] if weighted else None
    row = {"tower": int(ti), "name": towers.names[ti], "key": keys[ti],
           "photo_m": round(ph.combine_photo_heights(hs, devs), 1), "n_photos": len(hs),
           "n_confirmed": n_confirmed,
           "spread_m": round(float(np.ptp(hs)), 1) if len(hs) > 1 else 0.0,
           "photo_median_m": round(float(np.median(med)), 1), "top": top,
           "osm_m": float(towers.height_m[ti]), "truth_m": truth_m}
    if weighted:
        row["weight"] = round(sum(ph.photo_weight(d) for d in devs), 3)
    return row
