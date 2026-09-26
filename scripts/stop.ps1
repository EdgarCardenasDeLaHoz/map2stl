# Stop the strm2stl web app (uvicorn and its --reload workers).
# Matches on command line rather than netstat PIDs, which can be stale.

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'uvicorn|app\.server\.server|multiprocessing\.spawn' } |
    ForEach-Object {
        Write-Host "  Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
