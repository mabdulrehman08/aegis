<#
.SYNOPSIS
  Run the AEGIS backend so teammates' laptops can reach it over Tailscale.

.DESCRIPTION
  Binds the backend to this laptop's Tailscale address only, so it is reachable
  from machines in the team's tailnet but NOT from the rest of the university
  network. The backend has no authentication, so never bind it to 0.0.0.0 on
  campus WiFi.

  Prints the URL to give teammates (bridge, dashboard, camera).

.EXAMPLE
  .\hardware\scripts\host-backend.ps1
#>
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $tailscale -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) {
    $tailscale = "C:\Program Files\Tailscale\tailscale.exe"
}
if (-not $tailscale) {
    Write-Host "Tailscale is not installed: winget install --id tailscale.tailscale, then tailscale up" -ForegroundColor Red
    exit 1
}
$status = & $tailscale status --json | ConvertFrom-Json
if ($status.BackendState -ne "Running") {
    Write-Host "Tailscale is not connected. Run: tailscale up" -ForegroundColor Red
    exit 1
}
$ip = (& $tailscale ip -4 | Select-Object -First 1).Trim()
$name = $status.Self.HostName

Write-Host "=== AEGIS backend on the team tailnet (SIMULATION ONLY) ===" -ForegroundColor Cyan
Write-Host "Teammates use:  http://${ip}:$Port   (or http://${name}:$Port with MagicDNS)" -ForegroundColor Green
Write-Host "Bridge:         .\hardware\scripts\start-bridge.ps1 -Backend $name"
Write-Host "If Windows Firewall asks about Python, allow it on Private networks."
Write-Host "Use a single worker: simulation state lives in this process."

Push-Location $repoRoot
try {
    & uv run uvicorn backend.app.main:app --host $ip --port $Port --workers 1
} finally {
    Pop-Location
}
