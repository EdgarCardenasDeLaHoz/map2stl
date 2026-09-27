"""Tests for the in-app Guides (app/server/routers/guides.py + the /guides page in server.py).

Most tests point ``SOP_DIR`` at a temporary tree so they do not depend on the wording of the
real SOPs; one smoke test renders the checked-in ones.
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

from app.server.routers import guides as guides_router
from app.server.server import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16

GUIDE_A = """# SOP — Alpha guide

First paragraph of *alpha*
continues here, see `code_name`.

## 1. Intro

Text.

## 2. The process

1. **Pick the area** (Explore): do it.

   ![Pick](img/a/01-pick.png)
   *Caption one.*

2. **Load terrain**: then this. See [beta](beta.md#known-limits) and
   [external](https://example.com/x) and [local](#1-intro).

## 3. How it is built

1. **Not a step**: numbered list outside the process section.

| A | B |
|---|---|
| 1 | 2 |
"""

GUIDE_B = """# SOP — Beta

Beta summary.

## Known limits

- one
"""


@pytest.fixture()
def sop_dir(tmp_path, monkeypatch):
    d = tmp_path / "sop"
    (d / "img" / "a").mkdir(parents=True)
    (d / "alpha.md").write_text(GUIDE_A, encoding="utf-8")
    (d / "beta.md").write_text(GUIDE_B, encoding="utf-8")
    (d / "img" / "a" / "01-pick.png").write_bytes(PNG)
    (d / "img" / "a" / "notes.txt").write_text("not an image", encoding="utf-8")
    (tmp_path / "secret.md").write_text("# outside", encoding="utf-8")
    (tmp_path / "secret.png").write_bytes(PNG)
    monkeypatch.setattr(guides_router, "SOP_DIR", d)
    guides_router._cache.clear()
    yield d
    guides_router._cache.clear()


@pytest.fixture()
def client():
    return TestClient(app)


def test_list_titles_and_summaries(client, sop_dir):
    r = client.get("/api/guides")
    assert r.status_code == 200
    data = r.json()
    assert [g["slug"] for g in data] == ["alpha", "beta"]
    assert data[0]["title"] == "SOP — Alpha guide"
    assert data[0]["summary"] == "First paragraph of alpha continues here, see code_name."
    assert data[1]["summary"] == "Beta summary."


def test_new_sop_appears_automatically(client, sop_dir):
    (sop_dir / "gamma.md").write_text("# Gamma\n\nNew.\n", encoding="utf-8")
    assert "gamma" in [g["slug"] for g in client.get("/api/guides").json()]


def test_render_toc_and_steps(client, sop_dir):
    r = client.get("/api/guides/alpha")
    assert r.status_code == 200
    doc = r.json()
    assert doc["slug"] == "alpha" and doc["title"] == "SOP — Alpha guide"
    toc = doc["toc"]
    assert {"id": "1-intro", "text": "1. Intro", "level": 2} in toc
    steps = [t for t in toc if t["id"].startswith("step-")]
    assert steps == [
        {"id": "step-1", "text": "1. Pick the area", "level": 3},
        {"id": "step-2", "text": "2. Load terrain", "level": 3},
    ]
    html = doc["html"]
    assert 'id="step-1"' in html and 'id="step-2"' in html
    assert 'id="step-3"' not in html          # the list under "How it is built" is not a step
    assert 'class="headerlink"' in html        # permalink anchors
    assert "<table>" in html
    # The screenshot stays inside its step (3-space indented continuation).
    step1 = html.split('id="step-1"')[1].split('id="step-2"')[0]
    assert "01-pick.png" in step1


def test_image_urls_rewritten_and_served(client, sop_dir):
    html = client.get("/api/guides/alpha").json()["html"]
    assert 'src="/guides/img/a/01-pick.png"' in html
    assert 'src="img/' not in html
    r = client.get("/guides/img/a/01-pick.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG


def test_cross_links_rewritten(client, sop_dir):
    html = client.get("/api/guides/alpha").json()["html"]
    assert 'href="/guides/beta#known-limits"' in html
    assert 'href="#1-intro"' in html
    assert 'href="https://example.com/x"' in html and 'target="_blank"' in html


@pytest.mark.parametrize("slug", ["missing", "..%2Fsecret", "secret", "alpha.md", ".hidden"])
def test_unknown_or_traversal_slug_404(client, sop_dir, slug):
    assert client.get(f"/api/guides/{slug}").status_code == 404


@pytest.mark.parametrize("path", ["../../secret.png", "..%2F..%2Fsecret.png", "a/notes.txt",
                                  "a/missing.png", "a"])
def test_image_route_rejects_traversal_and_non_images(client, sop_dir, path):
    assert client.get(f"/guides/img/{path}").status_code == 404


def test_render_cached_per_mtime(sop_dir):
    first = guides_router.load_guide("beta")
    assert guides_router.load_guide("beta") is first
    path = sop_dir / "beta.md"
    path.write_text("# Beta two\n\nChanged.\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    again = guides_router.load_guide("beta")
    assert again is not first and again["title"] == "Beta two"


def test_page_routes(client, sop_dir):
    r = client.get("/guides")
    assert r.status_code == 200 and "/static/js/guides.js" in r.text
    assert client.get("/guides/alpha").status_code == 200
    assert client.get("/guides/nope").status_code == 404


def test_real_sops_render(client):
    """The checked-in SOPs: every image they reference exists and is served."""
    guides_router._cache.clear()
    data = client.get("/api/guides").json()
    slugs = {g["slug"] for g in data}
    assert {"city-stl-and-puzzle-sop", "large-region-sop"} <= slugs
    for slug in ("city-stl-and-puzzle-sop", "large-region-sop"):
        doc = client.get(f"/api/guides/{slug}").json()
        assert any(t["id"] == "step-1" for t in doc["toc"])
        srcs = re.findall(r'<img [^>]*src="([^"]+)"', doc["html"])
        assert srcs and all(s.startswith("/guides/img/") for s in srcs)
        for s in srcs:
            assert client.get(s).status_code == 200, s
    city = client.get("/api/guides/city-stl-and-puzzle-sop").json()["html"]
    assert 'href="/guides/large-region-sop"' in city
