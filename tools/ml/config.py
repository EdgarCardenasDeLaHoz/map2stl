"""
tools.ml.config — Shared constants, city definitions, and label sets.

All training scripts, evaluation tools, and notebooks import from here
so label ordering, city bboxes, and training defaults stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Roof shape labels  (order defines class indices — do NOT reorder)
# ---------------------------------------------------------------------------

SHAPE_LABELS: list[str] = [
    "flat", "gabled", "hipped", "pyramidal", "skillion", "dome",
]
LABEL_TO_IDX: dict[str, int] = {lbl: i for i, lbl in enumerate(SHAPE_LABELS)}
N_SHAPE_CLASSES: int = len(SHAPE_LABELS)

# Classes with low OSM coverage — oversampled during harvest
RARE_CLASSES: set[str] = {"pyramidal", "skillion", "dome"}


# ---------------------------------------------------------------------------
# City bounding boxes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitySpec:
    """Geographic bounding box for a training/eval city."""
    name: str
    north: float
    south: float
    east: float
    west: float
    description: str = ""
    # Approximate % of buildings with roof:shape in OSM (from eval_roof_tags)
    roof_tag_pct: float = 0.0

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Return (north, south, east, west)."""
        return (self.north, self.south, self.east, self.west)

    @property
    def area_km2(self) -> float:
        import math
        lat_km = (self.north - self.south) * 111.32
        lon_km = (self.east - self.west) * 111.32 * math.cos(
            math.radians((self.north + self.south) / 2)
        )
        return lat_km * lon_km


# Training cities — larger regions for more data
TRAIN_CITIES: dict[str, CitySpec] = {
    "Berlin": CitySpec(
        "Berlin", 52.555, 52.490, 13.440, 13.340,
        description="High roof:shape coverage (43%), diverse architecture",
        roof_tag_pct=43.2,
    ),
    "Vienna": CitySpec(
        "Vienna", 48.230, 48.170, 16.400, 16.320,
        description="Good coverage (2.2%), historic + modern mix",
        roof_tag_pct=2.2,
    ),
    "Barcelona": CitySpec(
        "Barcelona", 41.420, 41.350, 2.210, 2.120,
        description="Mediterranean flat roofs + Gothic quarter variety",
        roof_tag_pct=0.6,
    ),
    "Paris": CitySpec(
        "Paris", 48.875, 48.830, 2.370, 2.300,
        description="Mansard roofs, Haussmann architecture, domes",
        roof_tag_pct=1.5,
    ),
    "Amsterdam": CitySpec(
        "Amsterdam", 52.390, 52.340, 4.930, 4.860,
        description="Gabled canal houses, varied historic roofs",
        roof_tag_pct=2.3,
    ),
    "Prague": CitySpec(
        "Prague", 50.100, 50.055, 14.460, 14.390,
        description="Mixed Gothic/Baroque, good coverage (8.7%)",
        roof_tag_pct=8.7,
    ),
    "Rotterdam": CitySpec(
        "Rotterdam", 51.940, 51.895, 4.510, 4.430,
        description="Modern flat roofs + experimental architecture",
        roof_tag_pct=5.6,
    ),
}

# Evaluation cities — smaller regions for quick eval, non-overlapping with train
EVAL_CITIES: dict[str, CitySpec] = {
    "Amsterdam_eval": CitySpec(
        "Amsterdam_eval", 52.380, 52.355, 4.920, 4.880,
        description="ROOF-2 eval — Amsterdam historic centre",
        roof_tag_pct=2.3,
    ),
    "Vienna_eval": CitySpec(
        "Vienna_eval", 48.215, 48.190, 16.380, 16.340,
        description="ROOF-2 eval — Vienna inner city",
        roof_tag_pct=2.2,
    ),
    "Prague_eval": CitySpec(
        "Prague_eval", 50.090, 50.065, 14.450, 14.410,
        description="ROOF-2 eval — Prague old town",
        roof_tag_pct=8.7,
    ),
    "Berlin_eval": CitySpec(
        "Berlin_eval", 52.535, 52.510, 13.415, 13.375,
        description="ROOF-2 eval — Berlin Mitte",
        roof_tag_pct=43.2,
    ),
    "Rotterdam_eval": CitySpec(
        "Rotterdam_eval", 51.930, 51.905, 4.490, 4.450,
        description="ROOF-2 eval — Rotterdam centre",
        roof_tag_pct=5.6,
    ),
}

ALL_CITIES: dict[str, CitySpec] = {**TRAIN_CITIES, **EVAL_CITIES}


# ---------------------------------------------------------------------------
# Training defaults
# ---------------------------------------------------------------------------

# Crop / tile sizes
DEFAULT_CROP_SIZE: int = 128        # roof shape crops (up from 64)
DEFAULT_TILE_SIZE: int = 256        # height regression tiles
MAX_HEIGHT_M: float = 100.0         # clamp height predictions

# Training hyperparameters
DEFAULT_EPOCHS: int = 60
DEFAULT_BATCH_SIZE: int = 16
DEFAULT_LR: float = 3e-4
DEFAULT_WEIGHT_DECAY: float = 1e-4
EARLY_STOP_PATIENCE: int = 12       # epochs without val improvement

# ImageNet normalisation (for pretrained backbones)
IMAGENET_MEAN: tuple[float, ...] = (0.485, 0.456, 0.406)
IMAGENET_STD: tuple[float, ...] = (0.229, 0.224, 0.225)

# Model save paths (relative to strm2stl/)
DEFAULT_SHAPE_MODEL: str = "models/roofnet_shape_v2.pt"
DEFAULT_HEIGHT_MODEL: str = "models/roofnet_height_v3.pt"
