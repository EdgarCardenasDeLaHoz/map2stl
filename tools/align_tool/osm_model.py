"""Moved to ``city2stl.registration.osm_model`` (2026-09-27) so the web app can import it.

This name stays importable for the align tool's scripts: importing it binds the promoted
module itself, private names and module state included.
"""
import sys

from city2stl.registration import osm_model as _impl

sys.modules[__name__] = _impl
