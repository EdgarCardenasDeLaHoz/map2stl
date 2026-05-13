@echo off
setlocal enabledelayedexpansion

REM ====================================================================
REM  Playwright Standalone Browser Launcher + Test Runner
REM  This batch file launches Chrome and runs Playwright tests against it
REM ====================================================================

cd /d "%~dp0.." || exit /b 1

echo.
echo ╔════════════════════════════════════════════════════════╗
echo ║  3D Maps - Standalone Playwright Browser Test         ║
echo ╚════════════════════════════════════════════════════════╝
echo.

REM Check if Chrome is running
powershell -Command "Get-Process chrome -ErrorAction SilentlyContinue" >nul 2>&1
if errorlevel 1 (
    echo ✓ No existing Chrome process found
) else (
    echo ⚠ WARNING: Chrome is already running
    echo   Kill existing Chrome processes before starting? (This may close your browser)
    echo   Press Ctrl+C to cancel, or wait 5 seconds to continue...
    timeout /t 5 /nobreak
)

echo.
echo ▶ Starting standalone Chrome browser...
echo   - Opening on http://127.0.0.1:9001
echo   - Debug port: 9222
echo   - Profile directory: %TEMP%\chrome-playwright-*
echo.

REM Launch Chrome in a new window with debugging enabled
start "3D Maps - Playwright Chrome" powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& '%~dp0..\tools\playwright-standalone-browser.ps1' -DebugPort 9222 -Url 'http://127.0.0.1:9001'"

REM Wait for Chrome to start
echo Waiting 5 seconds for Chrome to initialize...
timeout /t 5 /nobreak

echo.
echo ▶ Running Playwright test script...
echo.

REM Run the Python test script
python "%~dp0..\tests\test-standalone-browser.py"

if errorlevel 1 (
    echo.
    echo ✗ Test script failed with error code !ERRORLEVEL!
    pause
    exit /b 1
) else (
    echo.
    echo ✓ Test completed successfully!
    pause
    exit /b 0
)
