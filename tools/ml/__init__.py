"""
tools.ml — Centralised ML training, evaluation, and data pipeline.

Inference lives in city2stl/ (roof_classifier, height/predict).
This package owns everything training-side: model definitions, data loading,
training loops, evaluation scripts, and city/label configuration.

Submodules
----------
config  — label sets, city bounding boxes, shared constants
models  — network architectures (RoofNetV2, MobileNetV3 backbone, legacy)
data    — datasets, transforms, harvest pipeline
train   — unified training loop with early stopping + LR scheduling
eval    — metrics, confusion matrices, per-class reporting
"""

__version__ = "0.1.0"
