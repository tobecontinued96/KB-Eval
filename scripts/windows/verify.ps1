<#
.SYNOPSIS
  Runs the local verification suite for Dify-KB-Eval.

.PARAMETER SkipSync
  Skip uv sync when the Python environment is already up to date.
#>

[CmdletBinding()]
param(
  [switch]$SkipSync
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Invoke-Step {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][scriptblock]$Command
  )

  Write-Host ""
  Write-Host "==> $Name" -ForegroundColor Cyan
  & $Command
  if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE."
  }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv was not found. Install uv or add it to PATH."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install Node.js/npm or add it to PATH."
}

if (-not $SkipSync) {
  Invoke-Step "Sync Python dependencies" {
    uv sync
  }
}

Invoke-Step "Run backend unit tests" {
  uv run python -m unittest discover
}

Invoke-Step "Check MarkItDown availability" {
  uv run python -c "from kb_eval.markitdown_converter import markitdown_available; raise SystemExit(0 if markitdown_available() else 1)"
}

$FrontendDir = Join-Path $ScriptDir "frontend"
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
  Invoke-Step "Install frontend dependencies" {
    Push-Location $FrontendDir
    try {
      npm install
    } finally {
      Pop-Location
    }
  }
}

Invoke-Step "Run frontend helper tests" {
  Push-Location $FrontendDir
  try {
    npm run test:helpers
  } finally {
    Pop-Location
  }
}

Invoke-Step "Build frontend" {
  Push-Location $FrontendDir
  try {
    npm run build
  } finally {
    Pop-Location
  }
}

Write-Host ""
Write-Host "Verification completed." -ForegroundColor Green
