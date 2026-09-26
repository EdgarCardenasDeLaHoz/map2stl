# Create (or refresh) a "3D Maps" Desktop shortcut that runs scripts\start.ps1.
# Safe to re-run: it overwrites the existing shortcut.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$start = Join-Path $PSScriptRoot 'start.ps1'

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '3D Maps.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$start`""
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'Launch the 3D Maps (strm2stl) web app in your browser'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\imageres.dll') + ',20'   # globe
$shortcut.Save()

Write-Host "Installed $shortcutPath -> $start" -ForegroundColor Green
