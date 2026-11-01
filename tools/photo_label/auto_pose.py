"""Compatibility shim: moved to `city2stl.photo_label.auto_pose` (F-UNIFY Phase 3).

`import auto_pose` (tool scripts, `register_model.py`) gets the package module itself, so
monkeypatches on either name hit the same object.
"""
import sys

from city2stl.photo_label import auto_pose as _m

sys.modules[__name__] = _m
