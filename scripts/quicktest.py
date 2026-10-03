"""Quick tests while developing: only what the uncommitted changes touch, Python and JS at once.

usage (from map2stl/):
    ~/.venvs/map2stl/Scripts/python.exe scripts/quicktest.py [--all-changed-since REF]

Picks:
  * Python: changed test files, and test files that import (or name) a changed module, plus the
    tests that failed last time (pytest --lf state); run with pytest-xdist when there are many.
  * JS: `vitest related` for changed .js / .ts / .vue files under app/client.
Both run concurrently. The full suite still runs in the pre-push hook (.githooks/pre-push).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PY_PACKAGES = ("app", "city2stl", "geo2stl", "tools")
JS_PREFIX = "app/client/"


def changed_files(since: str | None) -> list[str]:
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    files = set(git("diff", "--name-only", since or "HEAD"))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in files if (ROOT / f).exists())


def _module_names(path: str) -> set[str]:
    parts = Path(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    dotted = ".".join(parts)
    return {dotted, parts[-1]} if len(parts) > 1 else {dotted}


def python_targets(files: list[str]) -> list[str]:
    tests_dir = ROOT / "tests"
    targets = {f for f in files if f.startswith("tests/") and f.endswith(".py") and Path(f).name.startswith("test_")}
    modules = set()
    for f in files:
        if f.endswith(".py") and f.split("/")[0] in PY_PACKAGES:
            modules |= _module_names(f)
    if modules:
        dotted = [m for m in modules if "." in m]
        short = [m for m in modules if "." not in m]
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(m) for m in dotted) + r")\b"
            + (r"|\bimport (" + "|".join(map(re.escape, short)) + r")\b" if short else "")
            + (r"|from \S+ import [^\n]*\b(" + "|".join(map(re.escape, short)) + r")\b" if short else ""))
        for test in tests_dir.rglob("test_*.py"):
            try:
                if pattern.search(test.read_text(encoding="utf-8", errors="ignore")):
                    targets.add(test.relative_to(ROOT).as_posix())
            except OSError:
                continue
    return sorted(targets)


def run_python(targets: list[str]) -> int:
    if not targets:
        # Nothing changed on the Python side: just re-run what failed last time, if anything.
        cmd = [PY, "-m", "pytest", "-q", "--lf", "--lfnf=none", "tests"]
    else:
        cmd = [PY, "-m", "pytest", "-q", "--lf", "--lfnf=all", *targets]
        if len(targets) > 8:
            cmd += ["-n", "6"]
    print("python:", " ".join(cmd[3:]) if len(cmd) < 14 else f"{len(targets)} test files", flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def run_js(files: list[str]) -> int:
    js = [f for f in files if f.startswith(JS_PREFIX) and f.endswith((".js", ".ts", ".vue"))]
    js += [f for f in files if f.startswith("tests/js/")]
    if not js:
        print("js: nothing changed", flush=True)
        return 0
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    cmd = [npx, "vitest", "related", "--run", "--passWithNoTests", *js]
    print(f"js: vitest related ({len(js)} changed files)", flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all-changed-since", metavar="REF",
                    help="compare with REF (e.g. origin/master) instead of the last commit")
    a = ap.parse_args()
    files = changed_files(a.all_changed_since)
    print(f"{len(files)} changed files", flush=True)
    targets = python_targets(files)
    with ThreadPoolExecutor(max_workers=2) as pool:
        py = pool.submit(run_python, targets)
        js = pool.submit(run_js, files)
        rc_py, rc_js = py.result(), js.result()
    print(f"quicktest: python {'ok' if rc_py in (0, 5) else 'FAILED'}, js {'ok' if rc_js == 0 else 'FAILED'}")
    return 0 if rc_py in (0, 5) and rc_js == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
