# DriveSync 1-Click Zero-Admin Windows Deployment Script
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  DRIVESYNC - ZERO-ADMIN WINDOWS DEPLOYMENT INSTALLER   " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python installation
Write-Host "[1/3] Checking Python 3.11+ environment..." -ForegroundColor Yellow
try {
    $pythonVer = & python --version 2>&1
    Write-Host "  Detected: $pythonVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python 3.11+ is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.11 or higher from https://python.org before running." -ForegroundColor Red
    exit 1
}

# 2. Install DriveSync package in user mode
Write-Host "[2/3] Installing DriveSync package..." -ForegroundColor Yellow
& python -m pip install --user -e .

# 3. Run DriveSync doctor to verify installation and auto-download rclone if missing
Write-Host "[3/3] Running DriveSync environment health check & rclone setup..." -ForegroundColor Yellow
& python -m drivesync.cli doctor

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  DRIVESYNC INSTALLATION COMPLETE!                     " -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps to Start Syncing:" -ForegroundColor Yellow
Write-Host "  1. Authenticate Google Drive:" -ForegroundColor White
Write-Host "     drivesync auth login" -ForegroundColor Cyan
Write-Host "  2. Register local watched folder:" -ForegroundColor White
Write-Host "     drivesync folders add `"C:\Path\To\YourFolder`"" -ForegroundColor Cyan
Write-Host "  3. Start background sync service:" -ForegroundColor White
Write-Host "     drivesync start" -ForegroundColor Cyan
Write-Host "  4. Enable Windows User Autostart:" -ForegroundColor White
Write-Host "     drivesync autostart enable" -ForegroundColor Cyan
Write-Host ""
