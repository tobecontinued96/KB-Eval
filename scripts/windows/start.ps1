<#
.SYNOPSIS
  一键启动 Dify-KB-Eval 的后端 (uvicorn) + 前端 (vite dev) 服务。

.DESCRIPTION
  - 在两个独立的 PowerShell 窗口里分别起后端和前端，避免单窗口被阻塞。
  - 默认读取 frontend/.env / frontend/.env.local 里的 DEV_PORT；如果不存在则回退到 5598。
  - 后端默认 http://127.0.0.1:8200；启动后等待 /api/health 通畅再打开浏览器。
  - 每次启动后端前会自动执行 uv sync；前端在缺少 node_modules 时自动执行 npm install。
  - 关闭窗口即可停服务；脚本本身只负责启动和健康探测，不做 trap / 清理。

.PARAMETER Mock
  以前端 VITE_USE_MOCK=true 模式启动，跳过真实后端（仅适合纯前端验收）。

.EXAMPLE
  .\start.ps1
  .\start.ps1 -Mock
#>

[CmdletBinding()]
param(
  [switch]$Mock
)

$ErrorActionPreference = "Stop"

# 切到脚本所在目录（项目根），保证双击、相对路径都能跑
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ---- 端口探测（不写死，方便改 .env / .env.local） ----------------------------
$FrontendPort = 5598
@(
  (Join-Path $ScriptDir "frontend\.env"),
  (Join-Path $ScriptDir "frontend\.env.local")
) | ForEach-Object {
  if (Test-Path $_) {
    Get-Content $_ | ForEach-Object {
      if ($_ -match '^\s*DEV_PORT\s*=\s*(\d+)\s*$') {
        $FrontendPort = [int]$Matches[1]
      }
    }
  }
}
$BackendPort = 8200
$BackendUrl  = "http://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"
$HealthUrl   = "$BackendUrl/api/health"

Write-Host "==> Dify-KB-Eval 一键启动" -ForegroundColor Cyan
Write-Host "    后端: $BackendUrl"
Write-Host "    前端: $FrontendUrl"
Write-Host "    Mock: $(if ($Mock) { '开启' } else { '关闭' })"
Write-Host ""

# ---- 依赖命令预检 -------------------------------------------------------------
if (-not $Mock -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "未找到 uv，请先安装 uv 或确认 uv 已加入 PATH。"
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "未找到 npm，请先安装 Node.js / npm 或确认 npm 已加入 PATH。"
}

# ---- 端口占用预检 -------------------------------------------------------------
function Test-PortBusy {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return [bool]$conn
}

$BackendBusy = -not $Mock -and (Test-PortBusy $BackendPort)
$FrontendBusy = Test-PortBusy $FrontendPort

if ($BackendBusy) {
  Write-Warning "后端端口 $BackendPort 已被占用，假设已有后端在跑；继续启动前端。"
}
if ($FrontendBusy) {
  Write-Warning "前端端口 $FrontendPort 已被占用，假设已有前端在跑；稍后直接打开浏览器。"
}

# ---- 工具函数：把命令丢到新窗口里跑 -----------------------------------------
function Start-InNewWindow {
  param(
    [Parameter(Mandatory)][string]$Title,
    [Parameter(Mandatory)][string]$WorkingDir,
    [Parameter(Mandatory)][string]$CommandLine
  )
  # 编码用 UTF-8（防止 PowerShell 5.1 默认 GBK 把中文标签打乱）
  $psArgs = @(
    "-NoExit",
    "-Command",
    "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkingDir'; $CommandLine"
  )
  Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs -WorkingDirectory $WorkingDir | Out-Null
}

# ---- 启动后端 ----------------------------------------------------------------
$BackendDir = $ScriptDir
if (-not $Mock -and -not $BackendBusy) {
  # 启动 Postgres（如果 docker 在 PATH 上）。PG 不可用时给个警告，不阻断后端启动。
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "==> 启动 Postgres (docker compose up -d db)" -ForegroundColor Cyan
    docker compose up -d db | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "docker compose up -d db 失败，后端在首个请求时会再重试。"
    } else {
      Write-Host "==> 探测 127.0.0.1:5432 ..." -ForegroundColor Cyan
      $pgReady = $false
      for ($p = 1; $p -le 30; $p++) {
        $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue -InformationLevel Quiet
        if ($tcp) { $pgReady = $true; break }
        Start-Sleep -Seconds 1
      }
      if ($pgReady) {
        Write-Host "✔ Postgres 已就绪" -ForegroundColor Green
      } else {
        Write-Warning "Postgres 在 30 秒内未响应，请检查 `docker compose ps` / `docker compose logs db`。"
      }
    }
  } else {
    Write-Warning "未检测到 docker，假设已有外部 Postgres 在跑（按 DATABASE_URL 连接）。"
  }
  $BackendCmd = "uv sync; if (`$LASTEXITCODE -ne 0) { throw 'uv sync failed.' }; uv run uvicorn backend.app:app --host 127.0.0.1 --port $BackendPort"
  Start-InNewWindow -Title "Dify-KB-Eval · 后端 (8200)" -WorkingDir $BackendDir -CommandLine $BackendCmd
  Write-Host "✔ 后端已在新窗口启动" -ForegroundColor Green
}

# ---- 启动前端 ----------------------------------------------------------------
$FrontendDir = Join-Path $ScriptDir "frontend"
$ViteExtras = ""
if ($Mock) {
  $ViteExtras = "`$env:VITE_USE_MOCK='true';"
}
if (-not $FrontendBusy) {
  $FrontendCmd = "${ViteExtras}if (-not (Test-Path 'node_modules')) { npm install }; npm run dev"
  Start-InNewWindow -Title "Dify-KB-Eval · 前端 ($FrontendPort)" -WorkingDir $FrontendDir -CommandLine $FrontendCmd
  Write-Host "✔ 前端已在新窗口启动" -ForegroundColor Green
}

# ---- 等待后端健康检查（仅真实模式） -----------------------------------------
if (-not $Mock) {
  Write-Host "==> 等待后端健康检查: $HealthUrl"
  $ready = $false
  for ($i = 1; $i -le 30; $i++) {
    try {
      $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
      if ($resp.status -eq "ok") { $ready = $true; break }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if ($ready) {
    Write-Host "✔ 后端就绪" -ForegroundColor Green
  } else {
    Write-Warning "后端在 30 秒内未就绪，请检查后端窗口的报错。先帮你打开浏览器。"
  }
}

# ---- 打开浏览器 --------------------------------------------------------------
Write-Host "==> 打开浏览器: $FrontendUrl"
Start-Process $FrontendUrl | Out-Null

Write-Host ""
Write-Host "提示：关闭对应窗口即可停止服务。" -ForegroundColor DarkGray
