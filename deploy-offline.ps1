<#
.SYNOPSIS
  Load a Dify-KB-Eval offline package and start services with Docker Compose.

.EXAMPLE
  .\deploy-offline.ps1
  .\deploy-offline.ps1 -Down
#>

[CmdletBinding()]
param(
  [string]$PackageDir = "",
  [switch]$Down,
  [switch]$Logs,
  [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

if (-not $PackageDir) {
  $PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$PackageDir = (Resolve-Path $PackageDir).Path
Set-Location $PackageDir

function Assert-Command {
  param([Parameter(Mandatory)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name was not found. Install it or add it to PATH."
  }
}

function Read-EnvValue {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Default
  )
  $envFile = Join-Path $PackageDir ".env.offline"
  if (Test-Path $envFile) {
    foreach ($line in Get-Content -Encoding UTF8 $envFile) {
      if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*?)\s*$") {
        return $Matches[1].Trim().Trim('"').Trim("'")
      }
    }
  }
  return $Default
}

Assert-Command docker

$composeArgs = @("compose", "--project-name", "dify-kb-eval", "--env-file", ".env.offline", "-f", "docker-compose.offline.yml")

if ($Down) {
  Write-Host "==> Stopping offline services" -ForegroundColor Cyan
  & docker @composeArgs down
  exit $LASTEXITCODE
}

foreach ($required in @(".env.offline", "docker-compose.offline.yml")) {
  if (-not (Test-Path (Join-Path $PackageDir $required))) {
    throw "Missing $required in $PackageDir"
  }
}

$imageDir = Join-Path $PackageDir "images"
if (-not (Test-Path $imageDir)) {
  throw "Missing images directory: $imageDir"
}

Get-ChildItem -LiteralPath $imageDir -Filter "*.tar" | Sort-Object Name | ForEach-Object {
  Write-Host "==> Loading image $($_.Name)" -ForegroundColor Cyan
  $imageTar = $_.FullName
  docker load -i $imageTar
  if ($LASTEXITCODE -ne 0) {
    throw "docker load failed for $imageTar"
  }
}

foreach ($dir in @("datasets", "reports", "logs", "generated_sources", "config", "docs")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $PackageDir $dir) | Out-Null
}

Write-Host "==> Starting offline services" -ForegroundColor Cyan
& docker @composeArgs up -d
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$frontendPort = Read-EnvValue "FRONTEND_PORT" "5598"
$url = "http://127.0.0.1:$frontendPort"
$healthUrl = "$url/api/health"

Write-Host "==> Waiting for health check: $healthUrl" -ForegroundColor Cyan
$ready = $false
for ($i = 1; $i -le 90; $i++) {
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
  Write-Host "[+] Dify-KB-Eval is ready: $url" -ForegroundColor Green
  if (-not $NoOpen) {
    Start-Process $url | Out-Null
  }
} else {
  Write-Warning "Health check did not pass within 90 seconds. Run 'docker compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml logs backend frontend' for details."
}

Write-Host ""
Write-Host "Useful commands:"
Write-Host "  docker compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml ps"
Write-Host "  docker compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml logs -f backend frontend"
Write-Host "  .\deploy-offline.ps1 -Down"

if ($Logs) {
  & docker @composeArgs logs -f backend frontend
}
