"""Region-selection workflow e2e tests — sequential and rapid switching.

Ported from an exploratory Playwright audit (2026-07-19) that drove these flows
against a live server hunting for race conditions and stale-state bugs. These
pass now; they exist to catch regressions if the region-select/DEM-load wiring
changes later.
"""

from __future__ import annotations


def test_sequential_region_switch_updates_state(strict_page, live_server_url):
    """Selecting different regions in sequence updates appState.selectedRegion
    and its bbox each time — guards against stale/cached selection state."""
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")
    page.wait_for_function(
        "() => !!window.appState && typeof window.selectCoordinate === 'function'",
        timeout=10_000,
    )
    # Region list populates asynchronously after the initial page settle —
    # wait for the sidebar to actually render entries, not just for the app
    # bootstrap functions to exist.
    page.locator("span.coordinate-item-name").first.wait_for(state="visible", timeout=10_000)

    names = page.evaluate("() => (window.getCoordinatesData?.()||[]).map(r=>r.name)")
    assert len(names) >= 5, f"expected several saved regions, got {len(names)}"

    prev_bbox = None
    for name in names[:5]:
        ok = page.evaluate(
            """(name) => {
                const data = window.getCoordinatesData();
                const i = data.findIndex(r => r.name === name);
                if (i < 0) return false;
                window.selectCoordinate(i);
                return true;
            }""",
            name,
        )
        assert ok, f"could not select region '{name}'"
        page.wait_for_timeout(300)

        selected = page.evaluate("() => window.appState?.selectedRegion?.name")
        assert selected == name, (
            f"selected '{name}' but appState.selectedRegion is '{selected}' "
            "— selection state did not update"
        )

        bbox = page.evaluate(
            "() => { const r = window.appState?.selectedRegion; "
            "return r ? [r.north, r.south, r.east, r.west].join(',') : null; }"
        )
        assert bbox != prev_bbox, f"bbox unchanged after switching to '{name}' (stale bbox)"
        prev_bbox = bbox


def test_rapid_region_switch_settles_on_last_selection(strict_page, live_server_url):
    """Firing many region selections faster than any single load can settle
    must not leave a stale winner — the final state must match the last
    request, not an earlier one that finished later (a classic UI race)."""
    page = strict_page
    page.goto(live_server_url, wait_until="networkidle")
    page.wait_for_function(
        "() => !!window.appState && typeof window.selectCoordinate === 'function'",
        timeout=10_000,
    )
    page.locator("span.coordinate-item-name").first.wait_for(state="visible", timeout=10_000)

    names = page.evaluate("() => (window.getCoordinatesData?.()||[]).map(r=>r.name)")
    sample = names[:15]
    assert len(sample) >= 10, "need at least 10 regions to exercise the race"

    for name in sample:
        page.evaluate(
            """(name) => {
                const data = window.getCoordinatesData();
                const i = data.findIndex(r => r.name === name);
                if (i >= 0) window.selectCoordinate(i);
            }""",
            name,
        )
        page.wait_for_timeout(60)  # faster than a real load settles

    page.wait_for_timeout(2000)  # let everything that's in flight finish

    final = page.evaluate("() => window.appState?.selectedRegion?.name")
    assert final == sample[-1], (
        f"after rapid switching, selected='{final}' but the last request was "
        f"'{sample[-1]}' — a stale/earlier selection won the race"
    )
