# scripts/

Repository setup. Operator and research tools live in `tools/` and run as modules
from the repo root, e.g. `python -m tools.eval.eval_roof_tags`.

## `setup-venv.ps1`

Creates or refreshes `~/.venvs/map2stl` (never inside OneDrive), installs the pinned
`requirements*.txt`, installs this repo and `../numpy2stl` as editable packages, and
points git at the tracked hooks (`.githooks/`) and the nbstripout notebook filter.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
```

Run it once per PC, and again after changing a requirements file.

## Running the app

| Script | What it does |
|---|---|
| `start.ps1 [-Dev] [-Port 9000]` | Serves http://127.0.0.1:9000 and opens the browser; if the port already serves, just opens it. `-Dev` adds `--reload` and skips the browser. Called by `..\Start 3D Maps.bat`. |
| `stop.ps1` | Stops uvicorn and its `--reload` workers (matched on command line). Called by `..\Stop 3D Maps.bat`. |
| `install-shortcut.ps1` | Creates or refreshes a "3D Maps" Desktop shortcut that runs `start.ps1`. |

## `link-gitdir.ps1`

Keeps this repo's git database out of OneDrive: `.git` is a one-line file pointing at
`~/.gitdirs/<project>`. Run once per project per PC (on a second PC it creates that PC's
database from the remote without touching the working files).
