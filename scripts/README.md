# scripts/

Self-contained entry points for repeatable runs. Edit the constants at the top of each file and run.

## `train.py` — Retna height-model training

```
python scripts/train.py train       # plain training, resume from RESUME
python scripts/train.py grow        # grow/prune NAS (smart-init, widen-only)
python scripts/train.py deep        # grow/prune with deepen + widen
python scripts/train.py collect     # collect tiles only
python scripts/train.py full        # collect + train + inspect
python scripts/train.py inspect <ckpt-path>   # render 20-sample PDF
```

All artifacts:
- checkpoints → `models/`
- logs → `logs/`
- inspection PDFs → `output/<ckpt-stem>_inspect.pdf`

Why a Python script and not `.bat` / `.sh`?
- Same code runs on Windows, macOS, Linux — no parallel scripts to drift.
- Constants live in one editable block at the top.
- Subprocess+tee handles encoding (UTF-8) and live log streaming consistently.
- The script can be imported and reused from notebooks if needed.
