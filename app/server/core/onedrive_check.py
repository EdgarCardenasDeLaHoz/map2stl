"""Startup check for OneDrive damage to the code the server runs.

The project folder lives in OneDrive and two PCs share it. Two kinds of damage have reached the
source tree: conflict copies (``osm-DESKTOP-K0K0EAO.py``, written when both PCs edited the same
file; 28 of them on 2026-10-17) and cloud-only placeholders, which cannot be read while sync is
paused, so an import or a static file read fails. :func:`find_onedrive_problems` lists both;
the server logs them at startup so a broken page points at its cause.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

#: "<stem>-<COMPUTERNAME>[-<n>].<ext>", the name OneDrive gives the losing copy of a conflict.
CONFLICT_RE = re.compile(r"-(DESKTOP|LAPTOP)-[A-Z0-9]{5,}(-\d+)?(\.[^.]+)?$")

#: FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: the bytes are not on disk.
_CLOUD_ONLY = 0x1000 | 0x400000

_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".pytest_cache", "cache", "output", "runs"}

#: Only code and page assets matter for cloud-only: skyline run reports (2,357 PNGs on
#: 2026-10-17) are cloud-only by design and never read by the server.
_CODE_EXT = {".py", ".js", ".mjs", ".ts", ".vue", ".html", ".css", ".json", ".pyd", ".dll"}


def find_onedrive_problems(roots: list[Path]) -> tuple[list[Path], list[Path]]:
    """Return ``(conflict_copies, cloud_only)`` under *roots*, skipping caches and outputs."""
    conflicts: list[Path] = []
    cloud_only: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                if CONFLICT_RE.search(name):
                    conflicts.append(path)
                if path.suffix.lower() not in _CODE_EXT:
                    continue
                attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
                if attrs & _CLOUD_ONLY:
                    cloud_only.append(path)
    return conflicts, cloud_only


def log_onedrive_problems(repo_root: Path) -> None:
    """Warn about conflict copies and cloud-only files in the server's source trees."""
    roots = [repo_root / d for d in ("app", "city2stl", "geo2stl")]
    roots.append(repo_root.parent / "numpy2stl" / "numpy2stl")
    conflicts, cloud_only = find_onedrive_problems(roots)
    for path in conflicts:
        logger.warning("OneDrive conflict copy in the source tree (merge or delete): %s", path)
    for path in cloud_only:
        logger.warning("Cloud-only file in the source tree, unreadable while OneDrive is "
                       "paused (right-click > Always keep on this device): %s", path)
