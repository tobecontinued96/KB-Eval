<#
.SYNOPSIS
  Build and deploy Dify-KB-Eval with Docker Compose.

.EXAMPLE
  .\deploy-docker.ps1
  .\deploy-docker.ps1 -NoBuild
  .\deploy-docker.ps1 -Down
#>

[CmdletBinding()]
param(
  [switch]$NoBuild,
  [switch]$Pull,
  [switch]$Down,
  [switch]$Logs,
  [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Assert-Command {
  param([Parameter(Mandatory)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name was not found. Install it or add it to PATH."
  }
}

function Read-ComposeValue {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Default
  )

  $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ($processValue) {
    return $processValue
  }

  $envFile = Join-Path $ScriptDir ".env"
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.+?)\s*$") {
        return $Matches[1].Trim().Trim('"').Trim("'")
      }
    }
  }

  return $Default
}

Assert-Command docker

if ($Down) {
  Write-Host "==> Stopping Docker services"
  docker compose down
  exit $LASTEXITCODE
}

foreach ($dir in @("datasets", "datasets\generated", "reports", "logs", "generated_sources", "config")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir $dir) | Out-Null
}

if ($Pull) {
  Write-Host "==> Pulling base services"
  docker compose pull db
}

$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) {
  $composeArgs += "--build"
}
$composeArgs += @("db", "backend", "frontend")

Write-Host "==> docker $($composeArgs -join ' ')"
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$frontendPort = Read-ComposeValue -Name "FRONTEND_PORT" -Default "5598"
$url = "http://127.0.0.1:$frontendPort"
$healthUrl = "$url/api/health"

Write-Host "==> Waiting for health check: $healthUrl"
$ready = $false
for ($i = 1; $i -le 60; $i++) {
  try {
    $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($response.status -eq "ok") {
      $ready = $true
      break
    }
  } catch {
    Start-Sleep -Seconds 1
  }
}

if ($ready) {
  Write-Host "[+] Dify-KB-Eval is ready: $url"
  if (-not $NoOpen) {
    Start-Process $url | Out-Null
  }
} else {
  Write-Warning "Health check did not pass within 60 seconds. Run 'docker compose logs backend frontend' for details."
}

Write-Host ""
Write-Host "Useful commands:"
Write-Host "  docker compose ps"
Write-Host "  docker compose logs -f backend frontend"
Write-Host "  .\deploy-docker.ps1 -Down"

if ($Logs) {
  docker compose logs -f backend frontend
}
