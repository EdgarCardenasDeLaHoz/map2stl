"""The shared report shell (``city2stl.skyline.report_render``)."""
from __future__ import annotations

from city2stl.skyline.report_render import body_h1_css, kv_rows, page


def test_page_shell():
    out = page("T &amp; t", "body{}\n", "<p>x</p>", viewport=True)
    assert out.startswith('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n')
    assert '<meta name="viewport"' in out and "<title>T &amp; t</title>" in out
    assert "<style>\nbody{}\n</style>" in out
    assert out.endswith("<body>\n<p>x</p>\n</body>\n</html>\n")


def test_page_without_lang_or_viewport():
    out = page("t", "", "b", lang=None)
    assert "<html>" in out and "viewport" not in out


def test_kv_rows_escape_both_sides():
    assert kv_rows([("a<b", "c&d")], indent="") == "<tr><th>a&lt;b</th><td>c&amp;d</td></tr>"


def test_body_h1_css_width():
    assert "max-width: 1500px" in body_h1_css(1500)
