"""The trained flat-versus-pitched call, loaded from a checkpoint.

Why this exists at all. The hand-written cascade in :mod:`roof_classifier`
reads three numbers off the image and decides with fixed thresholds on them.
Measured on tagged cities it does not beat guessing: Salzburg came back 100 per
cent pitched, Cartagena 99.6 per cent pyramidal. A classifier that never says
"flat" is worse than no classifier, because the mesh already extrudes untagged
buildings flat -- the only thing such a model can do is put spikes on roofs
that were correct before it ran.

The checkpoint here was trained on roughly thirteen thousand OSM-tagged roofs
across forty-five cities and scored by holding out whole cities, since
neighbouring buildings share a block, a builder and a tagger. Held out, it
reaches 0.79 mean city AUC and beats always-flat in nine of the ten cities
whose tags describe their building stock rather than their landmarks.

Two decisions are baked into the checkpoint rather than rediscovered here:

  threshold   Stored, not 0.5. Errors are not symmetric -- a missed gable loses
              a ridge nobody notices, a wrongly pitched concrete block gets a
              three-metre spike. The stored cut holds the false-pitched rate
              near five per cent.
  features    Stored in order. :mod:`roof_features` must produce the same names
              in the same order or the model is reading the wrong columns, so
              the order is checked on load rather than trusted.

Resolution matters more than anything else here. ``fetch_region_satellite``
caps a job at 400 tiles and walks the zoom down until it fits, so a city-sized
request comes back at 0.8 to 1.2 m per pixel and a ten-metre house is eight
pixels across. Nothing can read a roof at eight pixels. So this module fetches
its own zoom-18 crop per building (0.4 to 0.6 m per pixel) and only falls back
to a caller-supplied crop when the tiles cannot be had.
"""
import logging
import os

import numpy as np

from city2stl import roof_features, roof_tiles

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "roof_shape_gbm.joblib")

# The model answers flat or pitched; it was never trained to tell a gable from
# a hip. Of the pitched roofs in the training tags, 14726 are gabled and 6232
# hipped, and the footprint's elongation separates the two at 0.505 AUC -- that
# is, not at all. So every pitched call is reported as the majority shape,
# which is honest about what was actually learned.
PITCHED_SHAPE = "gabled"
FLAT_SHAPE = "flat"

_cache = {}


class RoofShapeModel:
    """A loaded checkpoint plus the crop fetching it needs."""

    def __init__(self, payload, zoom=18):
        self.model = payload["model"]
        self.features = list(payload["features"])
        self.threshold = float(payload["threshold"])
        self.metrics = payload.get("metrics", {})
        self.zoom = zoom
        self._cols = [roof_features.FEATURES.index(f) for f in self.features]

    def row(self, ring, tags, crop=None):
        """The model's feature vector for one building, or None.

        *crop* is ``(rgb, north, south, east, west, m_per_px)``. When omitted
        the building's own zoom-18 tiles are fetched.
        """
        if crop is None:
            crop = roof_tiles.crop_for_ring(ring, zoom=self.zoom)
        if crop is None:
            return None
        feat = roof_features.extract(ring, tags or {}, crop)
        if feat is None:
            return None
        full = roof_features.to_row(feat)
        return [full[i] for i in self._cols]

    def predict_rows(self, rows):
        """Pitched probability for a list of feature vectors."""
        if not rows:
            return np.zeros(0, dtype=float)
        return self.model.predict_proba(np.asarray(rows, dtype=np.float64))[:, 1]

    def classify(self, ring, tags, crop=None):
        """``(shape, confidence)`` for one building, or ``(None, 0.0)``.

        Confidence is the probability of the class actually reported, so a
        consumer that wants only the calls the model is sure of can filter on
        it the same way it filters the hand-written tiers.
        """
        row = self.row(ring, tags, crop)
        if row is None:
            return None, 0.0
        p = float(self.predict_rows([row])[0])
        if p > self.threshold:
            return PITCHED_SHAPE, p
        return FLAT_SHAPE, 1.0 - p


def load(path=MODEL_PATH, zoom=18):
    """The checkpoint at *path*, or None when it is absent or unreadable.

    Absent is a normal state, not an error: a checkout without the model file
    should fall back to the hand-written tiers rather than fail to import.
    """
    key = (path, zoom)
    if key in _cache:
        return _cache[key]

    model = None
    if os.path.exists(path):
        try:
            import joblib

            payload = joblib.load(path)
            model = RoofShapeModel(payload, zoom=zoom)
        except ValueError as exc:
            # A feature name the extractor no longer produces. Loud, because
            # this means the checkpoint and the code have drifted apart and
            # every number downstream would be measured off the wrong columns.
            logger.error("roof model %s does not match roof_features: %s",
                         path, exc)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("could not load roof model %s: %s", path, exc)
    _cache[key] = model
    return model
