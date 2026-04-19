<#
.SYNOPSIS
    Start the strm2stl FastAPI dev server on port 9000.
    Shows a clear error if the port is already in use.
#>
$ErrorActionPreference = 'Stop'
$Port = 9000
$VenvPython = Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe'
$VenvPython = [System.IO.Path]::GetFullPath($VenvPython)

# ── Check Python ──────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Host ""
    Write-Host "  ERROR: Virtual-env Python not found at:" -ForegroundColor Red
    Write-Host "         $VenvPython" -ForegroundColor Yellow
    Write-Host "  Run:   python -m venv .venv && .venv\Scripts\pip install -r strm2stl\requirements.txt" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# ── Check port ────────────────────────────────────────────────
Write-Host "Checking port $Port..." -ForegroundColor Cyan
$conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object State -eq 'Listen'

if ($conn) {
    $procId = $conn.OwningProcess | Select-Object -First 1
    $proc   = Get-Process -Id $procId -ErrorAction SilentlyContinue

    Write-Host "" 
    Write-Host "  ============================================" -ForegroundColor Red
    Write-Host "  ERROR: Port $Port is already in use!" -ForegroundColor Red
    Write-Host "  ============================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Process : $($proc.ProcessName) (PID $procId)" -ForegroundColor Yellow
    Write-Host "  Command : $($proc.Path)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To fix:" -ForegroundColor Gray
    Write-Host "    1. Close the other server, or" -ForegroundColor Gray
    Write-Host "    2. Run the 'Stop Server' task (Ctrl+Shift+K)" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# ── Start server ──────────────────────────────────────────────
Write-Host "Starting server on http://127.0.0.1:$Port ..." -ForegroundColor Green
Write-Host "Press Ctrl+C or close this terminal to stop." -ForegroundColor DarkGray
Write-Host ""

Set-Location (Join-Path $PSScriptRoot '..')
& $VenvPython -m uvicorn app.server.server:app --host 127.0.0.1 --port $Port --reload
