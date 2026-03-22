"""
location_picker.py — DEPRECATED backward-compatibility shim.

The entry point has moved to server.py. Start the server with:
    python ui/server.py

This file is kept only to avoid breaking any scripts that still
reference `python ui/location_picker.py`. It will be removed in a
future cleanup.
"""
import warnings
warnings.warn(
    "location_picker.py is deprecated. Use 'python ui/server.py' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from .server import *  # noqa: F401, F403
from .server import run_server

if __name__ == "__main__":
    run_server()
