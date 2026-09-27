"""Register every plate we have, and say which registrations can be believed.

`export_align_data.py` already places a plate end to end: `locate` solves the centre, the
span and -- since the rotation was wired through -- the angle, the OSM window is fetched
around that answer, and `refine_guess` corrects the last few pixels. What it does not do is
say whether the result is right. Its gates are gates on the *refinement*: they decide whether
the correction is trustworthy enough to apply, not whether the placement underneath it is the
correct part of the city. A plate can sail through them while sitting a kilometre away, which
is exactly what the Paris miniature does.

Up to now that gap was filled by the hand alignments. Where ground truth existed we knew; where
it did not we guessed, and the guess was reported with the same confidence as the knowledge.
That does not survive contact with a new pack, so this module supplies the missing half:

  discover_packs()      every plate on disk, including ones nobody has listed yet
  register()            run the existing export over them
  crop_consensus()      an independent check that needs no ground truth
  verdict()             pass, review or fail, with the reason attached

The check is the part worth explaining. Correlation reports how well two rasters agree at the
position it likes best, which says nothing about whether a different position would have done
as well -- and in a uniformly dense city, one usually does. So instead of asking the whole map
one question, this asks separate pieces of it the same question and sees whether they give the
same answer. The OSM window is cut into tiles and each tile is correlated against the placed
plate on its own. A placement that is genuinely right puts every tile back where it came from.
A placement that is a saturation artifact has each tile drifting somewhere different, because
there was never a real correspondence holding them together -- only an average density that
looks alike everywhere.

That is a much harder test to pass by accident than a correlation peak, and it does not care
whether the plate has water, which is the property the four miniatures needed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import export_align_data as ex  # noqa: E402

DATA = ex.DATA


# The check itself (thresholds, crop_consensus, verdict, correct, score_export and the
# verdict writer) was promoted to city2stl.registration.consensus on 2026-09-27; the names
# are re-exported so `auto_register.crop_consensus` and friends keep working.
from city2stl.registration.consensus import (  # noqa: E402,F401
    AGREE_FRAC,
    FAIL_LEAD,
    MAX_CORRECTION_FRAC,
    MAX_CORRECTIONS,
    MIN_LEAD,
    MIN_TILE_FILL,
    MIN_TILE_OVERLAP,
    MIN_TILE_R,
    MIN_VOTERS,
    TILES,
    _place,
    _rival_offset,
    correct,
    crop_consensus,
    score_export,
    verdict,
)
from city2stl.registration.consensus import record_verdict as _record  # noqa: E402

# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _region_from_folder(folder: str) -> str:
    """A guess at what city a pack folder is for, from its name alone.

    Vendors name folders for humans, not for geocoders: "Barcelona,_Spain_-_S,_M,_L,_&_XL",
    "Philadelphia  PA - L   XL", "Denver Colorado 3D Miniature - 7029832". What survives all
    three is the front of the name, up to the point where the pack starts describing itself
    rather than the place. Everything from the first size list, catalogue number, or the word
    "miniature" onwards is dropped, and the separators are normalised back to commas.

    This is a starting point for a geocoder, not an answer. A pack whose folder name does not
    contain its city cannot be placed from its folder name, and `discover_packs` reports the
    guess so that a wrong one is visible before it is used.
    """
    s = folder.replace("_", " ")
    s = re.split(r"\b(?:3D\s+)?[Mm]iniature\b|\s-\s|\s+-\s+", s)[0]
    s = re.sub(r"\b[SMLX]{1,2}L?\b(?:\s*[,&]\s*)?", " ", s)     # S, M, L, XL size lists
    s = re.sub(r"\b\d{4,}\b", " ", s)                            # catalogue numbers
    s = re.sub(r"[,&]+", ",", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,-")
    # "Philadelphia  PA" collapses to "Philadelphia PA"; a bare state or country code reads
    # better to a geocoder with a comma in front of it.
    s = re.sub(r"^(.*?[^,\s])\s+([A-Z]{2})$", r"\1, \2", s)
    return s


def discover_packs() -> list[dict]:
    """Every plate directory under the pack roots, listed whether or not anyone declared it.

    `CITIES` is a hand-maintained list, so a pack dropped into the collection is invisible to
    the export until somebody edits the file. This walks the roots instead and returns both:
    the packs that are declared, with the region and span rules they were declared with, and
    the packs that are not, with a region guessed from the folder name and a note saying so.
    """
    declared = {folder: name for name, (_r, folder, _t) in ex.CITIES.items()}
    out = []
    for root in ex.PACK_ROOTS:
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            name = declared.get(d.name)
            if name is not None:
                region = ex.CITIES[name][0]
                known = True
            else:
                name = d.name
                region = _region_from_folder(d.name)
                known = False
            stl = ex.find_stl(d.name)
            out.append({
                "name": name,
                "folder": d.name,
                "root": str(root),
                "region": region,
                "declared": known,
                "stl": None if stl is None else str(stl),
                "water_stl": None if ex.find_water_stl(d.name) is None
                             else str(ex.find_water_stl(d.name)),
                "plate_key": ex.SLUGS.get(name),
                "slug": ex.SLUGS.get(name) or ex.slug(region),
            })
    return out


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------

# A plate's height scale, for a pack nobody has measured. `tallest_m` sets how the STL's z
# axis is read in metres, which the renderer uses and registration does not: `height_channel`
# normalises to unit maximum before anything is correlated, so the placement a discovered
# pack gets is the placement it would have got with the right number. It is a display default,
# not a guess that can move a plate.
DEFAULT_TALLEST_M = 200.0


def adopt(packs: list[dict]) -> list[str]:
    """Make undeclared packs exportable, and return the names that were adopted.

    `export_align_data.main` walks `CITIES`, so a pack that nobody has added to that dict is
    invisible to it however plainly it sits on disk. Rather than duplicate the export for
    discovered packs, the discovered pack is written into `CITIES` for the life of the
    process, with the region guessed from its folder name. That is the whole difference
    between the two kinds of pack: everything downstream -- the solve, the window, the
    refinement, the check -- is the same code either way.

    The entry is deliberately not written back to the source file. A guessed region is a
    starting point, and it should be confirmed by the plate registering against it before
    anyone commits it.
    """
    adopted = []
    for pack in packs:
        if pack["declared"] or pack["stl"] is None:
            continue
        ex.CITIES[pack["name"]] = (pack["region"], pack["folder"], DEFAULT_TALLEST_M)
        adopted.append(pack["name"])
    return adopted


def register(names: list[str] | None = None, *, export: bool = True,
             adopt_undeclared: bool = True, fix: bool = True,
             data_dir: Path = DATA) -> list[dict]:
    """Place the named plates and score every placement.

    With `export=False` nothing is fetched or solved and the plates already on disk are
    scored as they stand, which is what a re-check after a threshold change wants.
    """
    packs = {p["name"]: p for p in discover_packs()}
    if adopt_undeclared:
        taken = adopt([p for p in packs.values()
                       if names is None or p["name"] in names])
        for name in taken:
            print(f"{name}: not declared in CITIES; registering against the guessed region "
                  f"{packs[name]['region']!r}", flush=True)
    if export:
        ex.main(names)

    wanted = names or list(packs)
    results = []
    for name in wanted:
        pack = packs.get(name)
        slug = pack["slug"] if pack else ex.slug(name)
        scored = score_export(slug, data_dir, fix=fix)
        if scored is None:
            results.append({"slug": slug, "city": name, "status": "missing",
                            "reasons": ["no export on disk"]})
        else:
            if pack is not None and not pack["declared"]:
                scored["reasons"] = list(scored["reasons"]) + [
                    f"region {pack['region']!r} was guessed from the folder name"]
            _record(slug, scored, data_dir)
            results.append(scored)
    return results


def format_table(results: list[dict]) -> str:
    mark = {"pass": "PASS", "review": "REVIEW", "fail": "FAIL", "missing": "MISSING"}
    lines = [f"{'city':<26s} {'verdict':<8s} {'lead':>6s} {'tiles':>7s} {'fixed m':>7s} "
             f"{'rot':>7s} {'refine r':>9s}"]
    for r in sorted(results, key=lambda r: (r["status"] != "fail", r["status"] != "review",
                                            r.get("city", ""))):
        tiles = (f"{r.get('endorsing', 0)}/{r.get('voting', 0)}"
                 if r["status"] != "missing" else "-")
        lead = r.get("lead")
        miss = r.get("corrected_m")
        rot = r.get("rot_deg")
        rr = r.get("refine_r")
        lines.append(f"{r.get('city', r['slug']):<26s} {mark[r['status']]:<8s} "
                     f"{'' if lead is None else f'{lead:6.2f}'} {tiles:>7s} "
                     f"{'' if miss is None else f'{miss:7.0f}'} "
                     f"{'' if rot is None else f'{rot:+7.2f}'} "
                     f"{'' if rr is None else f'{rr:9.3f}'}")
        for why in r.get("reasons", []):
            lines.append(f"{'':<26s}   {why}")
    return "\n".join(lines)


USAGE = """auto_register.py [options] [city ...]

Register every plate on disk and report which placements can be believed.
With no city names, all of them.

  --discover     list every pack found, declared or not, and stop
  --score-only   do not export; score the plates already on disk
  --no-fix       report a bad placement without moving it onto the
                 position the tiles agree on
  --no-adopt     ignore packs that are not declared in CITIES

Exit status is 1 if any plate failed or is missing an export."""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if {"-h", "--help"} & set(argv):
        print(USAGE)
        return 0
    do_export = "--score-only" not in argv
    argv = [a for a in argv if a != "--score-only"]

    if "--discover" in argv:
        for p in discover_packs():
            flag = "" if p["declared"] else "   NOT DECLARED, region guessed"
            water = "water" if p["water_stl"] else "no water"
            print(f"{p['name']:<28s} {p['region']:<34s} {water:<9s} {p['slug']}{flag}")
        return 0

    adopt_undeclared = "--no-adopt" not in argv
    argv = [a for a in argv if a != "--no-adopt"]
    fix = "--no-fix" not in argv
    argv = [a for a in argv if a != "--no-fix"]
    results = register(argv or None, export=do_export,
                       adopt_undeclared=adopt_undeclared, fix=fix)
    print(format_table(results))
    bad = [r for r in results if r["status"] in ("fail", "missing")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
