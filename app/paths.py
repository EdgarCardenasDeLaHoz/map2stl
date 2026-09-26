"""The strm2stl checkout root, defined once.

Config, cache, tools and scripts resolve repo-relative files (models/, cache/,
output/, data/) from REPO_ROOT instead of counting ``.parent`` from their own
location, which silently breaks whenever a file moves.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]   # strm2stl/
