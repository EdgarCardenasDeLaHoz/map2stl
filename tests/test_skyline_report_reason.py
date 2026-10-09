"""The seed pages' "used" column (html_report._reading_status): a reading's reason is shown even
when its footprint has no measured row (review 2026-10-09, item 2: 27 tagged towers, Palmetto
among them, showed only "no published row")."""

from city2stl.skyline.html_report import _reading_status


def test_reason_is_shown_when_the_footprint_has_no_measured_row():
    seg = {"untrusted_reason": "depth_edge", "height_m": 51.0}
    assert _reading_status(seg, None, "seed_1") == ("no", "depth_edge")
    assert _reading_status({"behind": True}, None, "seed_1") == ("no", "tower behind")
    assert _reading_status({"top_edge": "roof"}, None, "seed_1") == ("no", "top edge: roof")
    assert _reading_status({"top_edge": "sky"}, None, "seed_1") == ("no", "no published row")


def test_used_and_unused_readings_of_a_measured_row_are_unchanged():
    row = {"per_seed_median_m": {"seed_1": 146.0}}
    assert _reading_status({"untrusted_reason": "x"}, row, "seed_1") == ("yes", "")
    assert _reading_status({"untrusted_reason": "tower_behind"}, row, "seed_4") == ("no", "tower_behind")
    assert _reading_status({}, row, "seed_4") == ("no", "")
