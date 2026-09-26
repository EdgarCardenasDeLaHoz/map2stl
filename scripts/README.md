# scripts/

Repository setup. Operator and research tools live in `tools/` and run as modules
from the repo root, e.g. `python -m tools.ml.train.train_retna`.

## `setup-venv.ps1`

Creates or refreshes `~/.venvs/strm2stl` (never inside OneDrive), installs the pinned
`requirements*.txt`, installs this repo and `../numpy2stl` as editable packages, and
points git at the tracked hooks (`.githooks/`) and the nbstripout notebook filter.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
```

Run it once per PC, and again after changing a requirements file.
