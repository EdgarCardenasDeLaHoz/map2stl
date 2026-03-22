"""
location_picker.py — backward-compatibility shim.

The server has been moved to server.py (backend refactor, step 7).
This file re-exports everything so existing scripts that do
`python ui/location_picker.py` or `from location_picker import app`
continue to work.
"""

from .server import *  # noqa: F401, F403
from .server import run_server

if __name__ == "__main__":
    run_server()
