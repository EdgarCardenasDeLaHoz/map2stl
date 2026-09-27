"""guides.py — the in-app Guides page: the SOPs under ``docs/sop/`` rendered as HTML.

Serves three things (the page itself, ``GET /guides`` and ``GET /guides/{slug}``, is
``guides_page`` in ``server.py`` beside ``reports_page``: template ``guides.html``, script
``static/js/guides.js``; the slug in the path, or ``/guides#<slug>/<anchor>``, only picks the
guide the page opens first):

* ``GET /api/guides`` — ``[{slug, title, summary}]`` for every ``docs/sop/*.md``: title is the
  first ``# `` heading, summary the first paragraph as plain text. A new SOP file appears here
  without any registration.
* ``GET /api/guides/{slug}`` — ``{slug, title, html, toc}``. Rendered here with the
  ``markdown`` package rather than in the browser so the page ships no Markdown parser and the
  image/link rewriting is tested in one place. ``toc`` lists the ``##``/``###`` headings and the
  numbered steps of the "process" section (``id`` ``step-N``), each ``{id, text, level}``.
* ``GET /guides/img/{path}`` — the screenshots under ``docs/sop/img/``, read-only, images only,
  with a traversal guard.

Relative URLs in the Markdown are rewritten while rendering: ``img/city/01-x.png`` becomes
``/guides/img/city/01-x.png`` and ``large-region-sop.md#5-known-limits`` becomes
``/guides/large-region-sop#5-known-limits``. Renders are cached per file mtime, so editing an SOP
shows up on the next request without a restart.
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath
import re
import threading
import xml.etree.ElementTree as etree
from pathlib import Path
from urllib.parse import urlsplit

import markdown
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from markdown.extensions import Extension
from markdown.extensions.toc import TocExtension
from markdown.treeprocessors import Treeprocessor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["guides"])

#: map2stl/ — routers -> server -> app -> map2stl
_MAP2STL = Path(__file__).resolve().parents[3]
#: Where the SOPs live. Tests point this at a temporary directory.
SOP_DIR = _MAP2STL / "docs" / "sop"

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
#: Heading text that marks the section whose top-level numbered list is the step list.
_PROCESS_RE = re.compile(r"\b(process|procedure|steps)\b", re.IGNORECASE)

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


# --- rendering ----------------------------------------------------------------

def _plain_text(el: etree.Element) -> str:
    """Text of ``el`` without the ``toc`` extension's permalink anchors."""
    parts = [el.text or ""]
    for child in el:
        if "headerlink" not in (child.get("class") or ""):
            parts.append(_plain_text(child))
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _rewrite_url(url: str, attr: str) -> str:
    """Map a URL relative to ``docs/sop/`` onto the app's routes; others are left alone."""
    if not url or url.startswith(("#", "/", "data:")):
        return url
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return url
    path = posixpath.normpath(parts.path)
    if path.startswith(".."):
        return url
    frag = f"#{parts.fragment}" if parts.fragment else ""
    if path.startswith("img/") and (attr == "src" or Path(path).suffix.lower() in _IMAGE_EXTS):
        return f"/guides/{path}"
    if "/" not in path and path.endswith(".md"):
        slug = path[:-3]
        if _SLUG_RE.match(slug):
            return f"/guides/{slug}{frag}"
    return url


class _GuideTreeprocessor(Treeprocessor):
    """Rewrites URLs, gives the process section's steps ids and collects the TOC.

    Runs after the ``toc`` extension (priority 5), so headings already carry their ids.
    """

    def run(self, root: etree.Element) -> None:
        for el in root.iter():
            if el.tag == "img" and el.get("src"):
                el.set("src", _rewrite_url(el.get("src"), "src"))
                el.set("loading", "lazy")
            elif el.tag == "a" and el.get("href"):
                href = el.get("href")
                new = _rewrite_url(href, "href")
                el.set("href", new)
                if urlsplit(new).scheme in ("http", "https"):
                    el.set("target", "_blank")
                    el.set("rel", "noopener")

        toc: list[dict] = []
        in_process = False
        steps_done = False
        for el in list(root):
            if el.tag in ("h2", "h3") and el.get("id"):
                text = _plain_text(el)
                toc.append({"id": el.get("id"), "text": text, "level": int(el.tag[1])})
                if el.tag == "h2":
                    in_process = bool(_PROCESS_RE.search(text)) and not steps_done
            elif el.tag == "ol" and in_process:
                for n, li in enumerate((c for c in el if c.tag == "li"), start=1):
                    sid = f"step-{n}"
                    li.set("id", sid)
                    li.set("class", "guide-step")
                    toc.append({"id": sid, "text": f"{n}. {_step_title(li)}", "level": 3})
                in_process = False
                steps_done = True
        self.md.guide_toc = toc


def _step_title(li: etree.Element) -> str:
    """The bold lead-in of a step (``**Pick the area**``), else its first words."""
    first = li[0] if len(li) and li[0].tag == "p" else li
    strong = first.find("strong")
    if strong is not None and (first.text or "").strip() == "":
        return _plain_text(strong)
    text = _plain_text(first)
    return text if len(text) <= 60 else text[:57].rstrip() + "…"


class _GuideExtension(Extension):
    def extendMarkdown(self, md):  # noqa: N802 (markdown API name)
        md.treeprocessors.register(_GuideTreeprocessor(md), "guide", 4)


def _new_markdown() -> markdown.Markdown:
    # tab_length=3: the SOPs indent list continuations (images, captions, sub-lists) by three
    # spaces, CommonMark style; at the default 4 every screenshot would end its step.
    return markdown.Markdown(tab_length=3, extensions=[
        "tables", "fenced_code", "attr_list", "sane_lists",
        TocExtension(permalink="#", permalink_title="Link to this section"),
        _GuideExtension(),
    ])


def _summary(text: str) -> str:
    """First paragraph after the title, as plain text."""
    para: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not para:
            if not s or s.startswith("#"):
                continue
            para.append(s)
        elif not s or s.startswith("#"):
            break
        else:
            para.append(s)
    out = " ".join(para)
    out = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", out)   # links/images -> their text
    out = re.sub(r"[`*]", "", out)
    return out


def _render(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    md = _new_markdown()
    html = md.convert(text)
    m = re.search(r"^# +(.+?)\s*#*\s*$", text, re.MULTILINE)
    return {
        "slug": path.stem,
        "title": m.group(1).strip() if m else path.stem,
        "summary": _summary(text),
        "html": html,
        "toc": getattr(md, "guide_toc", []),
    }


def guide_path(slug: str) -> Path:
    """``docs/sop/<slug>.md``, or 404 — only plain names of files directly in SOP_DIR."""
    if not _SLUG_RE.match(slug or ""):
        raise HTTPException(status_code=404, detail="Unknown guide")
    path = SOP_DIR / f"{slug}.md"
    if not path.is_file() or path.resolve().parent != SOP_DIR.resolve():
        raise HTTPException(status_code=404, detail="Unknown guide")
    return path


def load_guide(slug: str) -> dict:
    """Rendered guide, cached until the file's mtime changes."""
    path = guide_path(slug)
    mtime = path.stat().st_mtime
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    doc = _render(path)
    with _cache_lock:
        _cache[key] = (mtime, doc)
    return doc


def list_guides() -> list[dict]:
    if not SOP_DIR.is_dir():
        return []
    out = []
    for path in sorted(SOP_DIR.glob("*.md"), key=lambda p: p.name.lower()):
        if not _SLUG_RE.match(path.stem):
            continue
        try:
            doc = load_guide(path.stem)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Cannot render guide %s: %s", path, e)
            continue
        out.append({k: doc[k] for k in ("slug", "title", "summary")})
    return out


# --- routes -------------------------------------------------------------------

@router.get("/api/guides")
async def guides_index():
    return JSONResponse(list_guides())


@router.get("/api/guides/{slug}")
async def guide_detail(slug: str):
    doc = load_guide(slug)
    return JSONResponse({k: doc[k] for k in ("slug", "title", "html", "toc")})


@router.get("/guides/img/{rel_path:path}")
async def guide_image(rel_path: str):
    """One screenshot from ``docs/sop/img/`` (read-only, images only)."""
    root = (SOP_DIR / "img").resolve()
    try:
        target = (root / rel_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Not found") from None
    if (not target.is_relative_to(root) or not target.is_file()
            or target.suffix.lower() not in _IMAGE_EXTS):
        raise HTTPException(status_code=404, detail="Not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=mime or "application/octet-stream",
                        headers={"Cache-Control": "no-cache"})
