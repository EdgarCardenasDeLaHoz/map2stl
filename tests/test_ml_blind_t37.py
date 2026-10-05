"""T37 blind review: the photo-reading error study reproduces its headline."""

import pytest

pytest.importorskip("sklearn")


def test_t37_tag_rule_and_models(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    p = Path(__file__).resolve().parents[1] / "tools/ml/t37_photo_reading_errors.py"
    spec = importlib.util.spec_from_file_location("t37", p)
    t37 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t37)
    res = t37.run()
    assert res["n"] == {"miami": 388, "chicago": 2403}
    for out in res["directions"].values():
        rule = out["rule_osm_25pct"]
        assert rule["mae_kept"] < 0.7 * rule["mae_all"]       # the tag rule removes the bad tail
        assert out["gbm_all"]["auc"] > out["gbm_no_tag"]["auc"] > out["gbm_geometry_only"]["auc"]
