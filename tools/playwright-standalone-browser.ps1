# Playwright Standalone Browser Launcher
# Opens Chrome in a new window with debugging port enabled, then connects with Playwright

param(
    [string]$Url = "http://127.0.0.1:9001",
    [int]$DebugPort = 9222,
    [int]$WaitSeconds = 3
)

# Find Chrome executable
$ChromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$ChromeExe = $null
foreach ($path in $ChromePaths) {
    if (Test-Path $path) {
        $ChromeExe = $path
        break
    }
}

if (-not $ChromeExe) {
    Write-Error "Chrome not found in any standard location"
    exit 1
}

Write-Host "Found Chrome: $ChromeExe" -ForegroundColor Green

# Create a user profile directory for this session (isolated)
$ProfileDir = "$env:TEMP\chrome-playwright-$(Get-Random)"
New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null

Write-Host "Using profile directory: $ProfileDir" -ForegroundColor Cyan

# Launch Chrome with debugging port enabled
Write-Host "Launching Chrome on debug port $DebugPort..." -ForegroundColor Yellow
$ChromeProcess = Start-Process -FilePath $ChromeExe -ArgumentList @(
    "--remote-debugging-port=$DebugPort",
    "--user-data-dir=$ProfileDir",
    "--no-default-browser-check",
    "--new-window",
    $Url
) -PassThru

$ChromePid = $ChromeProcess.Id
Write-Host "Chrome started with PID: $ChromePid" -ForegroundColor Green

# Wait for CDP endpoint to come up
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$DebugPort/json/version" -Method Get -TimeoutSec 2
        $ready = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    Write-Error "Chrome launched, but CDP endpoint is not reachable on port $DebugPort"
    exit 1
}

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════╗"
Write-Host "║  Chrome Browser Launched Successfully                  ║"
Write-Host "║                                                        ║"
Write-Host "║  Debug Port: $DebugPort                                ║"
Write-Host "║  URL: $Url                      ║"
Write-Host "║  PID: $ChromePid                                      ║"
Write-Host "║                                                        ║"
Write-Host "║  Playwright can now connect to this browser.           ║"
Write-Host "║  Use: playwright.connect_over_cdp(                     ║"
Write-Host "║    'http://127.0.0.1:$DebugPort')                     ║"
Write-Host "╚════════════════════════════════════════════════════════╝"
Write-Host ""

# Keep the script running; press Ctrl+C to stop
Write-Host "Keeping Chrome running... (Press Ctrl+C to exit)"
$ChromeProcess | Wait-Process

# Cleanup
Write-Host "Cleaning up profile directory..." -ForegroundColor Yellow
Remove-Item -Path $ProfileDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green
