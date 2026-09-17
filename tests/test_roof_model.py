"""Tests for the trained roof-shape checkpoint and its wiring.

Nothing here touches the network. The one thing that would -- fetching a
building's own zoom-18 tiles -- is bypassed by handing `classify` a crop
directly, which is the same path the classifier takes when tiles are already
in hand.

The check that matters most is the feature order. The checkpoint stores the
names its columns were trained on; if `roof_features.FEATURES` is reordered or
renamed, every number the model reads is a different quantity and it will still
happily return probabilities. That failure is silent everywhere except here.
"""
import numpy as np
import pytest

from city2stl import roof_features, roof_model

_HAS_MODEL = roof_model.load() is not None
needs_model = pytest.mark.skipif(
    not _HAS_MODEL, reason="models/roof_shape_gbm.joblib not present")


def _ring(lon=13.4, lat=52.5, dx=0.00030, dy=0.00012):
    """A rectangular footprint of roughly 20 m by 13 m, closed."""
    return [[lon, lat], [lon + dx, lat], [lon + dx, lat + dy],
            [lon, lat + dy], [lon, lat]]


def _crop(ring, h=96, w=96, seed=0):
    """A synthetic crop covering *ring* with a margin, plus its geo bounds."""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    mx = 0.25 * (max(lons) - min(lons))
    my = 0.25 * (max(lats) - min(lats))
    rng = np.random.default_rng(seed)
    rgb = rng.integers(90, 160, size=(h, w, 3), dtype=np.uint8)
    return (rgb, max(lats) + my, min(lats) - my,
            max(lons) + mx, min(lons) - mx, 0.5)


class TestLoad:
    def test_missing_checkpoint_is_none_not_an_error(self):
        assert roof_model.load("no/such/checkpoint.joblib") is None

    @needs_model
    def test_checkpoint_reports_its_threshold_and_features(self):
        m = roof_model.load()
        assert 0.5 <= m.threshold <= 0.99
        assert len(m.features) == len(set(m.features))

    @needs_model
    def test_every_stored_feature_still_exists_in_the_extractor(self):
        m = roof_model.load()
        missing = [f for f in m.features
                   if f not in roof_features.FEATURES]
        assert missing == []

    @needs_model
    def test_column_indices_point_at_the_named_features(self):
        m = roof_model.load()
        assert [roof_features.FEATURES[i] for i in m._cols] == m.features


@needs_model
class TestClassify:
    def test_returns_a_known_shape_and_a_probability(self):
        m = roof_model.load()
        ring = _ring()
        shape, conf = m.classify(ring, {"building": "house"}, _crop(ring))
        assert shape in (roof_model.FLAT_SHAPE, roof_model.PITCHED_SHAPE)
        assert 0.0 <= conf <= 1.0

    def test_confidence_belongs_to_the_class_reported(self):
        """A flat call above 0.5 means the flat side, not the pitched one."""
        m = roof_model.load()
        ring = _ring()
        tags = {"building": "house"}
        crop = _crop(ring)
        row = m.row(ring, tags, crop)
        p = float(m.predict_rows([row])[0])
        shape, conf = m.classify(ring, tags, crop)
        if shape == roof_model.PITCHED_SHAPE:
            assert conf == pytest.approx(p)
            assert p > m.threshold
        else:
            assert conf == pytest.approx(1.0 - p)
            assert p <= m.threshold

    def test_degenerate_footprint_is_declined_not_guessed(self):
        m = roof_model.load()
        tiny = [[13.4, 52.5], [13.4000005, 52.5],
                [13.4000005, 52.5000005], [13.4, 52.5]]
        shape, conf = m.classify(tiny, {}, _crop(_ring()))
        assert shape is None
        assert conf == 0.0

    def test_row_length_matches_the_stored_feature_list(self):
        m = roof_model.load()
        ring = _ring()
        row = m.row(ring, {}, _crop(ring))
        assert len(row) == len(m.features)
