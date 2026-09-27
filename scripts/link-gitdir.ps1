# Keep this project's git database OUT of OneDrive, and leave the files in it.
#
# The project folder in OneDrive stays the source of truth: you edit, test and
# commit there. Only git's own database moves to ~/.gitdirs/<project>, and the
# project gets a one-line `.git` FILE pointing at it. OneDrive then syncs the
# working files (what it is built for) but never git's internals, which it
# corrupts: objects go cloud-only ("mmap failed", "bad object HEAD"), two PCs
# write one .git at once, and stale copies of .git/HEAD get restored.
#
# Run once per project per PC, from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\link-gitdir.ps1
#
# On a second PC the synced `.git` file already points at a path; this creates
# that PC's own database from the remote and leaves the working files alone.

param(
    [string]$Name = "strm2stl",
    # Set this default in the project's copy: on a second PC there is no local
    # database yet to read origin from.
    [string]$Remote = "https://github.com/EdgarCardenasDeLaHoz/strm2stl.git"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$gitdirs = Join-Path $HOME ".gitdirs"
$target = Join-Path $gitdirs $Name
$dotgit = Join-Path $root ".git"

if ((Test-Path $dotgit -PathType Leaf) -and (Test-Path (Join-Path $target "HEAD"))) {
    Write-Host "already linked: $dotgit -> $target"; exit 0
}
if (Test-Path $dotgit -PathType Container) {
    if (-not $Remote) { $Remote = git -C $root remote get-url origin }
    # Keep the old database as a backup outside OneDrive; nothing is deleted.
    $backup = "$target.onedrive-old"
    if (Test-Path $backup) { throw "backup already exists: $backup" }
    New-Item -ItemType Directory -Force $gitdirs | Out-Null
    Copy-Item -Recurse -Force $dotgit $backup
    # OneDrive pins/read-only-marks folders, which blocks removing them.
    attrib -R -P "$dotgit" /S /D | Out-Null; attrib -R -P "$dotgit" | Out-Null
    Remove-Item -Recurse -Force $dotgit
}
if (-not $Remote) { throw "pass -Remote <url>: no existing repository to read it from" }

if (-not (Test-Path (Join-Path $target "HEAD"))) {
    $tmp = Join-Path $gitdirs "_tmp-$Name"
    git clone --no-checkout $Remote $tmp
    Move-Item (Join-Path $tmp ".git") $target
    Remove-Item $tmp
}
$posix = $target -replace '\\', '/'
[IO.File]::WriteAllText($dotgit, "gitdir: $posix`n", (New-Object Text.UTF8Encoding $false))

# Point the index at the remote's default branch WITHOUT touching the files:
# whatever differs afterwards shows up in `git status` for you to review.
$branch = (git -C $root symbolic-ref --short refs/remotes/origin/HEAD) -replace '^origin/', ''
git -C $root reset -q "origin/$branch"
git -C $root status --short
Write-Host "linked: $dotgit -> $target (branch $branch; review git status above)"
