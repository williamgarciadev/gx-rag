# dev.ps1 — launch the gx-rag services natively on Windows (no Docker).
#
#   backend   FastAPI / uvicorn on :8050
#   frontend  Vite dev server on :3050, proxying /api to the local backend
#
# PowerShell equivalent of dev.sh (which is bash-only and does not run on Windows).
# Override ports with the BACKEND_PORT / FRONTEND_PORT environment variables.
#
# Usage:
#   .\dev.ps1
#   $env:BACKEND_PORT="8010"; .\dev.ps1
#
# Prerequisites (see docs/windows-native-setup.md):
#   py -m venv .venv
#   .venv\Scripts\pip install -r requirements.txt

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$BackendPort  = if ($env:BACKEND_PORT)  { $env:BACKEND_PORT }  else { "8050" }
$FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "3050" }
$Venv         = Join-Path $Root ".venv"
$Uvicorn      = Join-Path $Venv "Scripts\uvicorn.exe"

# --- preflight -------------------------------------------------------------
if (-not (Test-Path $Uvicorn)) {
    Write-Host "ERROR: $Venv not found or uvicorn missing. Create it first:" -ForegroundColor Red
    Write-Host "  py -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

$procs = @()

try {
    # --- backend -----------------------------------------------------------
    Write-Host "-> backend   http://127.0.0.1:$BackendPort   (API docs at /docs)"
    $procs += Start-Process -FilePath $Uvicorn `
        -ArgumentList "src.api:app", "--reload", "--port", $BackendPort `
        -WorkingDirectory $Root -NoNewWindow -PassThru

    # --- frontend (only if present) ---------------------------------------
    $Frontend = Join-Path $Root "frontend"
    if (Test-Path $Frontend) {
        if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
            Write-Host "   frontend: installing npm dependencies (first run)..."
            Push-Location $Frontend
            npm install
            Pop-Location
        }
        Write-Host "-> frontend  http://localhost:$FrontendPort"
        # The Vite dev proxy forwards /api to this backend (see frontend/vite.config.js).
        $env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"
        $procs += Start-Process -FilePath "npm.cmd" `
            -ArgumentList "run", "dev", "--", "--port", $FrontendPort `
            -WorkingDirectory $Frontend -NoNewWindow -PassThru
    }

    Write-Host ""
    Write-Host "Services starting — press Ctrl+C to stop." -ForegroundColor Green
    Wait-Process -Id $procs.Id
}
finally {
    Write-Host ""
    Write-Host "Shutting down services..."
    foreach ($p in $procs) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
