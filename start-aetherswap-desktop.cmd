@echo off
setlocal
cd /d "%~dp0"

set AETHERSWAP_MODE=desktop
set AETHERSWAP_HOST=127.0.0.1
set AETHERSWAP_PORT=28472
set AETHERSWAP_AGREE_DISCLAIMER=1
set AETHERSWAP_OPEN_BROWSER=1
set AETHERSWAP_HEADLESS=0
set AETHERSWAP_SERVER_ONLY=0
set AETHERSWAP_MANUAL_LOGIN_ONLY=0
set PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright

rem Keep AetherSwap networking independent from chat/browser proxy variables.
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set NO_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
set no_proxy=

echo Starting AetherSwap in desktop mode...
".venv\Scripts\python.exe" run.py

echo.
echo AetherSwap has stopped. Press any key to close this window.
pause >nul
endlocal
