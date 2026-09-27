# Start the map2stl web app on http://127.0.0.1:9000 and open it in the browser.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1            # desktop use
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Dev       # --reload, no browser
#
# If the port is already serving, the browser is pointed at it instead of
# starting a second copy. Close the window (or run scripts\stop.ps1) to stop.

param(
    [switch]$Dev,
    [int]$Port = 9000
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $HOME '.venvs\map2stl\Scripts\python.exe'
$url = "http://127.0.0.1:$Port"

if (-not (Test-Path $python)) {
    Write-Host "Python venv not found at $python" -ForegroundColor Red
    Write-Host "Create it with: powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1"
    exit 1
}

$client = New-Object System.Net.Sockets.TcpClient
try {
    $client.Connect('127.0.0.1', $Port)
    Write-Host "Already running on $url - opening it instead of starting a second copy."
    if (-not $Dev) { Start-Process $url }
    exit 0
} catch {
    # port is free
} finally {
    $client.Dispose()
}

$uvicornArgs = @('-m', 'uvicorn', 'app.server.server:app', '--host', '127.0.0.1', '--port', "$Port")
if ($Dev) {
    $uvicornArgs += '--reload'
} else {
    # Open the browser once the server has had a moment to bind.
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 4; Start-Process $u } -ArgumentList $url | Out-Null
}

Write-Host "Starting 3D Maps on $url  (close this window to stop)" -ForegroundColor Green
Set-Location $root
& $python @uvicornArgs
