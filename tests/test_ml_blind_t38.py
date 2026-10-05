"""T38 blind review: the untagged-height prior reproduces its headline (data in docs/research)."""

import pytest

pytest.importorskip("sklearn")


def test_t38_geometry_model_beats_constant_and_street_view(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    p = Path(__file__).resolve().parents[1] / "tools/ml/t38_untagged_prior.py"
    spec = importlib.util.spec_from_file_location("t38", p)
    t38 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t38)
    res = t38.run()
    pooled = res["pooled"]
    assert pooled["gbm_geometry"]["n"] == 872
    assert pooled["gbm_geometry"]["mae"] < pooled["constant"]["mae"] < pooled["street_view"]["mae"]
    assert all(10.0 <= c <= 14.0 for c in res["constants"].values())
