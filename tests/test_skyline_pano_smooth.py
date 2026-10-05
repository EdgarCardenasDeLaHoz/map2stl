"""Pano match smoothing against the per-view consensus (T25: no duplicate matches)."""

from types import SimpleNamespace

from city2stl.skyline._pano.detect import (
    _dedup_matches,
    _smooth_matches_across_views,
    _smooth_pano_matches_against_views,
)


def _seg(fid, diags=(), combined=0.0):
    return {"matched_projection": {"feature_id": fid, "x_px": 0} if fid else None,
            "matched_combined": combined,
            "match_diagnostics": [{"feature_id": f, "combined": c, "x_px": 5}
                                  for f, c in diags]}


def _views(*fid_lists):
    return [SimpleNamespace(matched_segments=[_seg(f) for f in fids]) for fids in fid_lists]


def _fids(segs):
    return [(s["matched_projection"] or {}).get("feature_id") for s in segs]


def test_pano_swap_onto_a_held_building_keeps_the_better_match():
    """Views agree on A. Pano segment 0 holds A; segment 1 (B, seen in no view) has A as a
    runner-up and is swapped onto it. Segment 0 scores A higher, so it keeps A and segment 1
    goes back to B instead of duplicating A."""
    views = _views(["A"], ["A"], ["A"])
    pano = SimpleNamespace(matched_segments=[
        _seg("A", [("A", 0.9)]),
        _seg("B", [("B", 0.6), ("A", 0.5)]),
    ])
    _smooth_pano_matches_against_views(pano, views)
    assert _fids(pano.matched_segments) == ["A", "B"]
    loser = pano.matched_segments[1]
    assert loser["match_smoothed"] is False
    assert loser["matched_projection_pre_dedup"]["feature_id"] == "A"


def test_pano_swapped_segment_wins_when_it_scores_higher():
    views = _views(["A"], ["A"])
    pano = SimpleNamespace(matched_segments=[
        _seg("A", [("A", 0.3)]),               # holds A, but scores it low
        _seg("B", [("B", 0.6), ("A", 0.8)]),   # swapped onto A, scores it higher
    ])
    # segment 0 already holds a popular feature, so only segment 1 is swapped
    _smooth_pano_matches_against_views(pano, views)
    assert _fids(pano.matched_segments) == [None, "A"]


def test_pano_swaps_without_conflict_are_unchanged():
    views = _views(["A", "C"], ["A", "C"])
    pano = SimpleNamespace(matched_segments=[
        _seg("A", [("A", 0.9)]),
        _seg("B", [("B", 0.6), ("C", 0.5)]),
    ])
    _smooth_pano_matches_against_views(pano, views)
    assert _fids(pano.matched_segments) == ["A", "C"]
    assert pano.matched_segments[1]["match_smoothed"] is True


def test_two_swapped_losers_cannot_both_restore_onto_one_feature():
    segs = [_seg("A", [("A", 0.9)]), _seg("A", [("A", 0.5)]), _seg("A", [("A", 0.4)])]
    for s in segs[1:]:
        s["matched_projection_pre_smoothing"] = {"feature_id": "B"}
    assert _dedup_matches(segs, restore_swapped=True) == 2
    assert _fids(segs) == ["A", "B", None]


def test_per_view_smoothing_still_clears_duplicates():
    """The per-view pass now shares the helper; its losers are cleared, not restored."""
    rows = [SimpleNamespace(matched_segments=[_seg("X", [("X", 0.2), ("A", 0.7)]),
                                              _seg("Y", [("Y", 0.2), ("A", 0.4)])],
                            raw_image=None),
            SimpleNamespace(matched_segments=[_seg("A")], raw_image=None),
            SimpleNamespace(matched_segments=[_seg("A")], raw_image=None)]
    _smooth_matches_across_views(rows)
    assert _fids(rows[0].matched_segments) == ["A", None]
