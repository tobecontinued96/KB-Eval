<#
.SYNOPSIS
  Build Docker images and create an offline deployment package.

.EXAMPLE
  .\build-offline-package.ps1
  .\build-offline-package.ps1 -Tag v20260625 -IncludeRuntimeData
#>

[CmdletBinding()]
param(
  [string]$Tag = (Get-Date -Format "yyyyMMdd-HHmmss"),
  [string]$OutputRoot = "offline-packages",
  [string]$BackendImage = "",
  [string]$FrontendImage = "",
  [string]$PostgresImage = "",
  [switch]$SkipPull,
  [switch]$IncludeRuntimeData,
  [switch]$NoZip
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

function Read-EnvValue {
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
    foreach ($line in Get-Content -Encoding UTF8 $envFile) {
      if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.+?)\s*$") {
        return $Matches[1].Trim().Trim('"').Trim("'")
      }
    }
  }

  return $Default
}

function Invoke-Step {
  param(
    [Parameter(Mandatory)][string]$Title,
    [Parameter(Mandatory)][scriptblock]$Command
  )
  Write-Host "==> $Title" -ForegroundColor Cyan
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Title failed with exit code $LASTEXITCODE"
  }
}

function Copy-DirectoryIfExists {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$DestinationRoot
  )
  $source = Join-Path $ScriptDir $Name
  $destination = Join-Path $DestinationRoot $Name
  if (Test-Path $source) {
    Copy-Item -LiteralPath $source -Destination $DestinationRoot -Recurse -Force
  } else {
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
  }
}

Assert-Command docker

$BackendImage = if ($BackendImage) { $BackendImage } else { Read-EnvValue "BACKEND_IMAGE" "dify-kb-eval-backend:$Tag" }
$FrontendImage = if ($FrontendImage) { $FrontendImage } else { Read-EnvValue "FRONTEND_IMAGE" "dify-kb-eval-frontend:$Tag" }
$PostgresImage = if ($PostgresImage) { $PostgresImage } else { Read-EnvValue "POSTGRES_IMAGE" "m.daocloud.io/docker.io/library/postgres:16" }

$PythonImage = Read-EnvValue "PYTHON_IMAGE" "m.daocloud.io/docker.io/library/python:3.12-slim"
$NodeImage = Read-EnvValue "NODE_IMAGE" "m.daocloud.io/docker.io/library/node:22-alpine"
$NginxImage = Read-EnvValue "NGINX_IMAGE" "m.daocloud.io/docker.io/library/nginx:1.27-alpine"
$AptMirror = Read-EnvValue "APT_MIRROR" "http://mirrors.aliyun.com/debian"
$AptSecurityMirror = Read-EnvValue "APT_SECURITY_MIRROR" "http://mirrors.aliyun.com/debian-security"
$PypiIndexUrl = Read-EnvValue "PYPI_INDEX_URL" "https://mirrors.aliyun.com/pypi/simple/"
$NpmRegistry = Read-EnvValue "NPM_REGISTRY" "https://registry.npmmirror.com"

$PostgresDb = Read-EnvValue "POSTGRES_DB" "dify_kb_eval"
$PostgresUser = Read-EnvValue "POSTGRES_USER" "dify_kb_eval"
$PostgresPassword = Read-EnvValue "POSTGRES_PASSWORD" "dify_kb_eval"
$FrontendPort = Read-EnvValue "FRONTEND_PORT" "5598"
$BackendPort = Read-EnvValue "BACKEND_PORT" "8200"
$NetworkName = Read-EnvValue "DOCKER_NETWORK_NAME" "dify-kb-eval-net"
$RunDbBootstrap = Read-EnvValue "RUN_DB_BOOTSTRAP" "true"
$RunDbMigrations = Read-EnvValue "RUN_DB_MIGRATIONS" "true"
$RunDbInitOnEmpty = Read-EnvValue "RUN_DB_INIT_ON_EMPTY" "true"
$RunDbStampHeadOnInit = Read-EnvValue "RUN_DB_STAMP_HEAD_ON_INIT" "true"

Invoke-Step "Build backend image $BackendImage" {
  docker build `
    -t $BackendImage `
    --build-arg "PYTHON_IMAGE=$PythonImage" `
    --build-arg "APT_MIRROR=$AptMirror" `
    --build-arg "APT_SECURITY_MIRROR=$AptSecurityMirror" `
    --build-arg "PYPI_INDEX_URL=$PypiIndexUrl" `
    -f Dockerfile .
}

Invoke-Step "Build frontend image $FrontendImage" {
  docker build `
    -t $FrontendImage `
    --build-arg "NODE_IMAGE=$NodeImage" `
    --build-arg "NGINX_IMAGE=$NginxImage" `
    --build-arg "NPM_REGISTRY=$NpmRegistry" `
    -f frontend/Dockerfile frontend
}

if (-not $SkipPull) {
  Invoke-Step "Pull Postgres image $PostgresImage" {
    docker pull $PostgresImage
  }
} else {
  Invoke-Step "Check local Postgres image $PostgresImage" {
    docker image inspect $PostgresImage
  }
}

$packageName = "dify-kb-eval-offline-$Tag"
$outputRootPath = Join-Path $ScriptDir $OutputRoot
$packageDir = Join-Path $outputRootPath $packageName
$imageDir = Join-Path $packageDir "images"

if (Test-Path $packageDir) {
  throw "Package directory already exists: $packageDir"
}

New-Item -ItemType Directory -Force -Path $imageDir | Out-Null

$backendTar = Join-Path $imageDir "backend.tar"
$frontendTar = Join-Path $imageDir "frontend.tar"
$postgresTar = Join-Path $imageDir "postgres.tar"

Invoke-Step "Save backend image" {
  docker save -o $backendTar $BackendImage
}
Invoke-Step "Save frontend image" {
  docker save -o $frontendTar $FrontendImage
}
Invoke-Step "Save Postgres image" {
  docker save -o $postgresTar $PostgresImage
}

Copy-Item -LiteralPath (Join-Path $ScriptDir "docker-compose.offline.yml") -Destination $packageDir -Force
Copy-Item -LiteralPath (Join-Path $ScriptDir "deploy-offline.ps1") -Destination $packageDir -Force
Copy-Item -LiteralPath (Join-Path $ScriptDir "deploy-offline.sh") -Destination $packageDir -Force
Copy-Item -LiteralPath (Join-Path $ScriptDir "deploy-offline.bat") -Destination $packageDir -Force

Copy-DirectoryIfExists "datasets" $packageDir
Copy-DirectoryIfExists "docs" $packageDir
Copy-DirectoryIfExists "config" $packageDir

foreach ($dir in @("reports", "logs", "generated_sources")) {
  if ($IncludeRuntimeData) {
    Copy-DirectoryIfExists $dir $packageDir
  } else {
    New-Item -ItemType Directory -Force -Path (Join-Path $packageDir $dir) | Out-Null
  }
}

$envOffline = @"
POSTGRES_DB=$PostgresDb
POSTGRES_USER=$PostgresUser
POSTGRES_PASSWORD=$PostgresPassword
POSTGRES_IMAGE=$PostgresImage

BACKEND_IMAGE=$BackendImage
FRONTEND_IMAGE=$FrontendImage
BACKEND_PORT=$BackendPort
FRONTEND_PORT=$FrontendPort
DOCKER_NETWORK_NAME=$NetworkName

DOCKER_DATABASE_URL=
RUN_DB_BOOTSTRAP=$RunDbBootstrap
RUN_DB_MIGRATIONS=$RunDbMigrations
RUN_DB_INIT_ON_EMPTY=$RunDbInitOnEmpty
RUN_DB_STAMP_HEAD_ON_INIT=$RunDbStampHeadOnInit
DB_WAIT_TIMEOUT_SECONDS=60

LOG_LEVEL=INFO
LOG_DIR=logs
LOG_TO_FILE=true
EVAL_RUNNER_CONCURRENCY=8
EVAL_RUNNER_TICK_MS=500
EVAL_RUNNER_SUBPROCESS=enabled
MINERU_API_TOKEN=
MARKITDOWN_COMMAND=
"@
$envOffline | Set-Content -Encoding UTF8 -Path (Join-Path $packageDir ".env.offline")

$manifest = @"
Dify-KB-Eval offline package
created_at=$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
backend_image=$BackendImage
frontend_image=$FrontendImage
postgres_image=$PostgresImage
frontend_url=http://127.0.0.1:$FrontendPort

Deploy on the offline machine:
  Windows: .\deploy-offline.ps1
  Linux/macOS: bash ./deploy-offline.sh
"@
$manifest | Set-Content -Encoding UTF8 -Path (Join-Path $packageDir "OFFLINE-MANIFEST.txt")

function New-OfflinePackageZip {
  <#
  .SYNOPSIS
    Build the offline package zip file-by-file so a single locked/skipped
    file does not abort the whole archive (the way Compress-Archive does).
  .DESCRIPTION
    Walks $packageDir recursively and writes each file into $zipPath via
    System.IO.Compression.ZipArchive. Files that raise a sharing-violation
    IOException are retried briefly; if still unavailable, the path is
    recorded in $skippedList and the file is omitted from the archive.
  #>
  param(
    [Parameter(Mandatory)][string]$SourceDir,
    [Parameter(Mandatory)][string]$DestinationZip,
    [int]$RetryCount = 5,
    [int]$RetryDelayMs = 500
  )

  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem

  $skipped = New-Object System.Collections.Generic.List[string]
  $entryCount = 0
  $entryBytes = 0L

  if (Test-Path -LiteralPath $DestinationZip) {
    Remove-Item -LiteralPath $DestinationZip -Force
  }

  $zipStream = [System.IO.File]::Open(
    $DestinationZip,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::ReadWrite
  )
  try {
    $archive = New-Object System.IO.Compression.ZipArchive(
      $zipStream,
      [System.IO.Compression.ZipArchiveMode]::Create
    )
    try {
      $sourceFull = (Resolve-Path -LiteralPath $SourceDir).ProviderPath
      $sourceFullLen = $sourceFull.Length + 1  # include trailing separator

      # Enumerate via .NET to avoid PowerShell path-resolution codepage issues
      $allFiles = [System.IO.Directory]::EnumerateFiles(
        $sourceFull,
        '*',
        [System.IO.SearchOption]::AllDirectories
      )

      foreach ($abs in $allFiles) {
        $rel = $abs.Substring($sourceFullLen).Replace('\', '/')

        $written = $false
        $lastErr = $null
        for ($i = 0; $i -lt $RetryCount; $i++) {
          try {
            $entry = $archive.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
            $entryStream = $entry.Open()
            try {
              $srcStream = [System.IO.File]::Open(
                $abs,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
              )
              try {
                $srcStream.CopyTo($entryStream)
              } finally {
                $srcStream.Dispose()
              }
            } finally {
              $entryStream.Dispose()
            }
            $entryCount++
            $entryBytes += (Get-Item -LiteralPath $abs).Length
            $written = $true
            break
          } catch [System.IO.IOException] {
            $lastErr = $_
            if ($i -lt $RetryCount - 1) {
              Start-Sleep -Milliseconds $RetryDelayMs
            }
          } catch [System.UnauthorizedAccessException] {
            $lastErr = $_
            break
          }
        }

        if (-not $written) {
          $skipped.Add($rel)
          Write-Warning ("Skipped (locked/unreadable after {0} retries): {1} -- {2}" -f $RetryCount, $rel, $lastErr.Exception.Message)
        }
      }
    } finally {
      $archive.Dispose()
    }
  } finally {
    $zipStream.Dispose()
  }

  return [pscustomobject]@{
    EntryCount  = $entryCount
    EntryBytes  = $entryBytes
    Skipped     = $skipped.ToArray()
    Destination = $DestinationZip
  }
}

if (-not $NoZip) {
  $zipPath = "$packageDir.zip"
  Write-Host "==> Compress offline package" -ForegroundColor Cyan
  $zipResult = New-OfflinePackageZip -SourceDir $packageDir -DestinationZip $zipPath

  Write-Host ("[+] Offline package: {0} ({1} entries, {2:N1} MB)" -f `
    $zipResult.Destination, `
    $zipResult.EntryCount, `
    ($zipResult.EntryBytes / 1MB)) -ForegroundColor Green

  if ($zipResult.Skipped.Count -gt 0) {
    $skippedLine = "skipped_files=" + ($zipResult.Skipped -join ',')
    Add-Content -Encoding UTF8 -Path (Join-Path $packageDir "OFFLINE-MANIFEST.txt") -Value $skippedLine
    Write-Warning ("{0} file(s) were skipped because they were locked. They are listed in OFFLINE-MANIFEST.txt." -f $zipResult.Skipped.Count)
  }
} else {
  Write-Host "[+] Offline package directory: $packageDir" -ForegroundColor Green
}

Write-Host ""
Write-Host "Copy the package to the offline machine, extract it, then run:"
Write-Host "  .\deploy-offline.ps1"
Write-Host "  bash ./deploy-offline.sh"
