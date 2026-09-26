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
    [string]$Name = "strm2stl",
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

if (Test-Path (Join-Path $root "pyproject.toml")) {
    # the dev extra when the project declares one, the plain install otherwise
    & $vpy -m pip install -e "$root[dev]"
    if ($LASTEXITCODE -ne 0) { & $vpy -m pip install -e "$root" }
} elseif (Test-Path (Join-Path $root "requirements.txt")) {
    $reqs = @("-r", (Join-Path $root "requirements.txt"))
    $dev = Join-Path $root "requirements-dev.txt"
    if (Test-Path $dev) { $reqs += @("-r", $dev) }
    & $vpy -m pip install @reqs
}
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

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

Write-Host "venv ready: $vpy"
