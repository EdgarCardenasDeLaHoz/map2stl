"""Execute a notebook's code cells in-process.  Used for headless runs."""
from __future__ import annotations
import json
import os
import sys
import traceback
from pathlib import Path

import matplotlib
matplotlib.use("Agg")


def run_notebook(nb_path: str | Path, working_dir: str | Path | None = None) -> bool:
    nb_path = Path(nb_path)
    if working_dir:
        os.chdir(working_dir)

    with open(nb_path, encoding="utf-8") as fh:
        nb = json.load(fh)

    ns: dict = {"__name__": "__main__"}
    for i, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", []))
        if not src.strip():
            continue
        print(f"\n=== Cell {i} ===", flush=True)
        try:
            exec(compile(src, f"<{nb_path.name} cell {i}>", "exec"), ns)
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc(limit=8)
            return False

    print("\n=== DONE ===", flush=True)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tools.ml.run_notebook <notebook.ipynb> [cwd]")
        sys.exit(1)
    nb = sys.argv[1]
    cwd = sys.argv[2] if len(sys.argv) > 2 else None
    ok = run_notebook(nb, cwd)
    sys.exit(0 if ok else 1)
