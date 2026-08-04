@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set NO_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
set no_proxy=

echo Steam history one-request probe
echo.
echo Requirements:
echo   1. AetherSwap is fully closed.
echo   2. VPN/Clash and UU are fully exited, including background processes.
echo   3. Steam++ Steam acceleration is running.
echo.
echo The script will abort if these conditions are not met.
echo It will send at most ONE Steam price-history HTTP request.
echo.
choice /C YN /N /M "Continue? [Y/N]: "
if errorlevel 2 goto :end

".venv\Scripts\python.exe" "diagnostics\steam_history_once.py"
echo.

:end
pause
endlocal