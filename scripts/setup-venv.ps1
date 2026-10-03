# Create (or refresh) this project's virtualenv OUTSIDE the project folder.
#
# Projects live in OneDrive. A venv inside one is ~12k files OneDrive tries to
# sync and then turns into cloud-only placeholders Python cannot read, and a
# venv hard-codes absolute paths, so a synced copy breaks on the other machine.
# It goes in ~/.venvs/<project> instead, and bytecode goes in ~/.cache/pycache.
#
# Not %LOCALAPPDATA%: sandboxed (MSIX) apps get writes there redirected into
# their private storage, so a venv made from one would be invisible elsewhere.
#
# The same venv serves ../numpy2stl (its tests run from pytest.ini here).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1 -Python C:\Python312\python.exe

param(
    [string]$Python = "python",
    [string]$Name = "map2stl",
    [string]$Venv = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Venv) { $Venv = Join-Path $HOME ".venvs\$Name" }

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $Python -m venv $Venv
}
$vpy = Join-Path $Venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip

# Pinned versions first (requirements*.txt), then both repos as editable
# packages so app / geo2stl / city2stl / numpy2stl import from any directory.
$numpy2stl = Join-Path (Split-Path -Parent $root) "numpy2stl"
& $vpy -m pip install -r (Join-Path $root "requirements.txt") -r (Join-Path $root "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install of requirements failed" }
& $vpy -m pip install --no-deps -e $numpy2stl -e $root
if ($LASTEXITCODE -ne 0) { throw "editable install failed (is ../numpy2stl checked out?)" }

# Neural models (skyline SegFormer, tools/ml) on the GPU when there is one. The plain PyPI
# torch wheel is CPU-only on Windows, so CUDA was unreachable; pin the CUDA 12.4 build
# (GTX 1650, driver 566: SegFormer b3 18x faster than on CPU, 2026-10-03).
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    & $vpy -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
    if ($LASTEXITCODE -ne 0) { throw "CUDA torch install failed" }
    & $vpy -m pip install "transformers>=4.40"
}

# Keep bytecode out of the synced folder for every run of this venv, not only
# the ones launched through a script: a .pth line runs at interpreter start.
$site = & $vpy -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
$cache = Join-Path $HOME ".cache\pycache"
Set-Content -Encoding ascii -Path (Join-Path $site "zz_pycache_prefix.pth") `
    -Value "import sys; sys.pycache_prefix = sys.pycache_prefix or r'$cache'"

# .gitattributes routes notebooks through nbstripout with required=true, so git
# fails on any notebook until the filter points at a python that has it.
$gitPy = $vpy -replace '\\', '/'
git -C $root config filter.nbstripout.clean "`"$gitPy`" -m nbstripout"
git -C $root config filter.nbstripout.smudge cat
git -C $root config filter.nbstripout.required true
git -C $root config diff.ipynb.textconv "`"$gitPy`" -m nbstripout -t"
# Tracked hooks: ruff on staged files (pre-commit), test suite (pre-push).
git -C $root config core.hooksPath .githooks
# ../numpy2stl has its own tracked pre-push hook (its tests). Also set by its
# scripts/link-gitdir.ps1; skipped when that repo is not linked on this PC yet.
if (Test-Path (Join-Path $numpy2stl ".git")) { git -C $numpy2stl config core.hooksPath .githooks }

Write-Host "venv ready: $vpy"
