@echo off
REM ============================================================
REM  Dify-KB-Eval one-click starter
REM  Backend: 127.0.0.1:8200
REM  Frontend: DEV_PORT from frontend\.env/.env.local, fallback 5598
REM
REM  Usage:
REM    Double-click start.bat
REM    start.bat -Mock
REM ============================================================

setlocal EnableExtensions

cd /d "%~dp0"

set "FRONTEND_PORT=5598"
for %%F in ("frontend\.env" "frontend\.env.local") do (
  if exist "%%~F" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%%~F") do (
      if /i "%%A"=="DEV_PORT" set "FRONTEND_PORT=%%B"
    )
  )
)

set "BACKEND_PORT=8200"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%"
set "HEALTH_URL=http://127.0.0.1:%BACKEND_PORT%/api/health"

set "MOCK_MODE=0"
if /i "%1"=="-Mock" set "MOCK_MODE=1"
if /i "%1"=="/Mock" set "MOCK_MODE=1"

echo.
echo ==^> Dify-KB-Eval start
echo     Backend:  http://127.0.0.1:%BACKEND_PORT%
echo     Frontend: %FRONTEND_URL%
echo     Mock:     %MOCK_MODE%
echo.

where npm >nul 2>&1
if errorlevel 1 (
  echo [x] npm was not found. Please install Node.js/npm or add npm to PATH.
  goto :fatal
)

if "%MOCK_MODE%"=="0" (
  where uv >nul 2>&1
  if errorlevel 1 (
    echo [x] uv was not found. Please install uv or add uv to PATH.
    goto :fatal
  )
)

set "BACKEND_BUSY=0"
set "FRONTEND_BUSY=0"

netstat -ano | findstr /R "[: ]%BACKEND_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  if "%MOCK_MODE%"=="0" (
    set "BACKEND_BUSY=1"
    echo [!] Backend port %BACKEND_PORT% is already in use. Reusing existing backend.
  )
)

netstat -ano | findstr /R "[: ]%FRONTEND_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  set "FRONTEND_BUSY=1"
  echo [!] Frontend port %FRONTEND_PORT% is already in use. Reusing existing frontend.
)

if "%MOCK_MODE%"=="0" if "%BACKEND_BUSY%"=="0" (
  REM 启动 Postgres（如果可用）。PG 不可用时给个警告，不阻断后端启动，
  REM 让没装 docker 的开发者能照常用文件式 / Mock 模式开发。
  where docker >nul 2>&1
  if not errorlevel 1 (
    echo ==^> Starting Postgres via docker compose...
    docker compose up -d db >nul 2>&1
    if errorlevel 1 (
      echo [!] docker compose up -d db failed. Backend will retry on first request.
    ) else (
      echo [+] Postgres container requested. Probing 127.0.0.1:5432 ...
      set "PG_READY=0"
      for /l %%p in (1,1,30) do (
        powershell -NoProfile -Command "try { (Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue -InformationLevel Quiet) | %% { if ($_) { exit 0 } else { exit 1 } } } catch { exit 1 }" >nul 2>&1
        if not errorlevel 1 (
          set "PG_READY=1"
          goto :pg_ok
        )
        >nul timeout /t 1 /nobreak
      )
    )
    :pg_ok
    if "%PG_READY%"=="1" (
      echo [+] Postgres is reachable on 127.0.0.1:5432.
    ) else (
      echo [!] Postgres did not respond in 30s. Check `docker compose ps` / `docker compose logs db`.
    )
  ) else (
    echo [!] docker not on PATH. Assuming an external Postgres is reachable via DATABASE_URL.
  )
  start "Dify-KB-Eval Backend" cmd /k "uv sync || exit /b 1 & uv run uvicorn backend.app:app --host 127.0.0.1 --port %BACKEND_PORT%"
  echo [+] Backend window started.
)

if "%FRONTEND_BUSY%"=="0" (
  if "%MOCK_MODE%"=="1" (
    start "Dify-KB-Eval Frontend MOCK" cmd /k "set VITE_USE_MOCK=true&&cd /d frontend&&if not exist node_modules (npm install || exit /b 1) & npm run dev"
  ) else (
    start "Dify-KB-Eval Frontend" cmd /k "cd /d frontend&&if not exist node_modules (npm install || exit /b 1) & npm run dev"
  )
  echo [+] Frontend window started.
)
echo.

if "%MOCK_MODE%"=="1" goto :open_browser

echo ==^> Waiting for backend health: %HEALTH_URL%
set "READY=0"
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 2; if ($r.status -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :health_ok
  )
  >nul timeout /t 1 /nobreak
)

:health_ok
if "%READY%"=="1" (
  echo [+] Backend is ready.
) else (
  echo [!] Backend was not ready in 30 seconds. Check the backend window.
)

:open_browser
echo ==^> Opening browser: %FRONTEND_URL%
start "" "%FRONTEND_URL%"
echo.
echo Tip: close the Backend/Frontend windows to stop services.
echo.
goto :end

:fatal
echo.
echo Press any key to close this window.
pause >nul

:end
endlocal
