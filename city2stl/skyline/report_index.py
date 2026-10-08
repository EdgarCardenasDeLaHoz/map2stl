"""Parse the per-seed statistics back out of a rendered region report ``index.html``.

The pano summary table written by the region report is the only machine-readable
copy of these numbers (no sidecar carries them), so both readers — the static
landing page (``scripts/build_landing_page.py``) and the app's ``/api/reports/index``
— parse it with this one definition of the row format.
"""

from __future__ import annotations

import re

#: Regions whose seeds were hand-picked; other seeds are "seed" / "auto" / "web".
CURATED_REGIONS = {"cartagena", "chicago", "miami"}

# Matches a row in the pano summary <tbody>:
#   <tr><td><a href="seed_N.html">seed_1</a></td>
#       <td>corr</td><td>nseg</td><td>nm</td>
#       <td>rate%</td><td>ncov</td>
#       <td style="background:rgba(...)">quality_label</td></tr>
# Since 2026-10-08 (F-SKY26 2f) the row also has a kind cell (street / drone) after the seed,
# "used" and "tag within 25 %" cells after coverage, and a drone seed's match rate is "n/a" in a
# span; older reports, without them, still parse.
ROW_RE = re.compile(
    r'<tr><td><a href="([^"]+)">([^<]+)</a></td>'   # (url, name)
    r'(?:<td>(?:street|drone)</td>)?'                 # kind (2026-10-08)
    r'<td>[^\n]*?</td>'                               # heading correction
    r'<td>(\d+)</td>'                                 # detected
    r'<td>(\d+)</td>'                                 # matched
    r'<td>((?:[^<]|<span[^>]*>[^<]*</span>)+)</td>'   # match rate  e.g. "84%", drone "n/a"
    r'<td>(\d+)</td>'                                 # coverage
    r'(?:<td>\d+</td><td>(?:[^<]|<span[^>]*>[^<]*</span>)*</td>)?'  # used, tag within 25 %
    r'<td[^>]*?background:([^"]+)"[^>]*>([^<]+)</td>'# bgcolor, quality label
    r'</tr>',
    re.DOTALL,
)

#: Quality background colour → canonical state (coarse: good / medium / weak).
BG_TO_QUALITY = {
    "rgba(46,160,67":  "good",
    "rgba(230,180,40": "medium",
    "rgba(214,40,40":  "weak",    # original red + F-DET3 "no detection"
    "rgba(200,60,20":  "weak",    # F-DET3 "mismatch"
    "rgba(200,130,20": "weak",    # F-DET3 "low coverage"
    "rgba(160,160,160": "weak",   # drone seed with fewer than 3 tagged readings (2026-10-08)
}


def stat(txt: str, label: str) -> int:
    """The number in the summary card labelled *label* (``"seeds"``, ...), or 0."""
    m = re.search(
        r'<div class="num">\s*([0-9]+)\s*</div>\s*' + re.escape(label), txt)
    return int(m.group(1)) if m else 0


def seed_source(seed_name: str, region: str) -> str:
    """``"web"``, ``"auto"``, ``"curated"`` or ``"seed"`` for one seed."""
    if seed_name.startswith("web_"):
        return "web"
    if seed_name.startswith("auto"):
        return "auto"
    return "curated" if region in CURATED_REGIONS else "seed"


def parse_pano_rows(txt: str) -> list[dict]:
    """Rows of the pano summary table, in page order.

    Each row: ``url`` (relative to the report dir), ``seed``, ``detected``,
    ``matched``, ``match_rate`` (e.g. ``"84%"``), ``coverage``, ``quality``
    (good / medium / weak) and ``quality_label``.
    """
    rows = []
    for m in ROW_RE.finditer(txt):
        rel_url, seed_name, nseg, nm, rate, ncov, bgcolor, qlabel = m.groups()
        rows.append({
            "url": rel_url,
            "seed": seed_name,
            "detected": int(nseg),
            "matched": int(nm),
            "match_rate": re.sub(r"<[^>]+>", "", rate).strip(),
            "coverage": int(ncov),
            "quality": next((v for k, v in BG_TO_QUALITY.items() if k in bgcolor), "weak"),
            "quality_label": qlabel.strip(),
        })
    return rows
