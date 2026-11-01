"""The shared HTML report base (F-UNIFY Phase 2, part B).

Every skyline report page (the per-seed and index pages in ``html_report``, the benchmark
page in ``benchmark_report``, the height-diagnostic report, the all-regions landing page and
the Commons photo-groups review) is built from the same pieces, kept here:

- :func:`esc`: the one escaping function (``html.escape``);
- :func:`page`: the page shell (doctype, ``<head>``, ``<style>``, ``<body>``);
- :data:`THEME_CSS` and the small CSS helpers shared by several pages;
- :func:`kv_rows`: the ``<tr><th>key</th><td>value</td></tr>`` rows of a summary table.

A page's own CSS and body stay with the page: the pages look different on purpose. Pure
string functions, no I/O, no imports from the rest of the package, so every report module can
import it without a cycle. The numpy2stl registration report is not built from this (Phase 3).
"""
from __future__ import annotations

import html
from collections.abc import Iterable

#: The one escaping function every report uses.
esc = html.escape

_VIEWPORT = '<meta name="viewport" content="width=device-width,initial-scale=1">\n'

#: Light/dark colour tokens for the ``html_report`` pages (2026-10-08: in a dark-scheme browser
#: the pages had no background and #222 text, near-black on black). Appended last in each
#: page's ``<style>``, so it overrides the element colours above it; rules with their own light
#: background (the selected tab, tinted table rows) keep dark text and stay readable.
THEME_CSS = """
  :root { color-scheme: light dark; --bg: #ffffff; --ink: #222222; --muted: #666666;
          --line: #dddddd; --th: #f4f6fa; --accent: #0a3070; --stat: #f4f6fa;
          --bad-bg: #fdecec; --track: #eef1f6; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #15171c; --ink: #e8eaed; --muted: #a9adb4; --line: #3a3f47;
            --th: #242a33; --accent: #9dbcff; --stat: #242a33; --bad-bg: #4a2427;
            --track: #2a2f37; }
  }
  body { background: var(--bg); color: var(--ink); }
  h1 { border-bottom-color: var(--accent); }
  h2, .talloc-title { color: var(--accent); }
  th, td { border-color: var(--line); }
  th { background: var(--th); }
  a, nav.breadcrumb a { color: var(--accent); }
  .small, .missing, td.note, .talloc-num { color: var(--muted); }
  .talloc-label { color: var(--ink); }
  .talloc-track { background: var(--track); }
  .stat { background: var(--stat); }
  .stat .num { color: var(--accent); }
  figure.tabs .tab-labels label { background: var(--th); border-color: var(--line); }
  .estimates tr.disagree { background: var(--bad-bg); }
  details > summary { cursor: pointer; font-weight: 600; margin: 0.6em 0; }
  .tsw { display: inline-block; width: 11px; height: 11px; border-radius: 3px;
         margin-right: 5px; vertical-align: -1px; }
  .tier { white-space: nowrap; cursor: help; border-bottom: 1px dotted var(--muted); }
  td.unused { color: var(--muted); }
"""


def body_h1_css(max_width_px: int) -> str:
    """The ``body`` and ``h1`` rules the ``html_report`` pages share (only the width differs)."""
    return f"""  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: {max_width_px}px; margin: 1.5em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #0a3070; padding-bottom: 0.2em; }}
"""


#: The breadcrumb rules of the ``html_report`` pages.
BREADCRUMB_CSS = """  nav.breadcrumb { font-size: 0.9em; margin-bottom: 1em; }
  nav.breadcrumb a { color: #0a3070; text-decoration: none; }
"""


def page(title_html: str, css: str, body: str, *, lang: str | None = "en",
         viewport: bool = False) -> str:
    """One complete page. ``title_html`` is already escaped; ``css`` goes in ``<style>`` and
    ``body`` between ``<body>`` and ``</body>``. ``lang=None`` omits the ``lang`` attribute;
    ``viewport`` adds the responsive viewport meta."""
    lang_attr = f' lang="{lang}"' if lang else ""
    return (f"<!doctype html>\n<html{lang_attr}>\n<head>\n<meta charset=\"utf-8\">\n"
            f"{_VIEWPORT if viewport else ''}<title>{title_html}</title>\n"
            f"<style>\n{css}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def kv_rows(pairs: Iterable[tuple[str, str]], indent: str = "    ") -> str:
    """``<tr><th>key</th><td>value</td></tr>`` per pair, both escaped, joined by newlines."""
    return "\n".join(f"{indent}<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in pairs)
