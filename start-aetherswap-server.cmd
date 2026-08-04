@echo off
cd /d "%~dp0"
set AETHERSWAP_MODE=server
set AETHERSWAP_HOST=127.0.0.1
set AETHERSWAP_PORT=28472
set AETHERSWAP_AGREE_DISCLAIMER=1
set AETHERSWAP_OPEN_BROWSER=0
set PLAYWRIGHT_BROWSERS_PATH=%CD%\.playwright
".venv\Scripts\python.exe" -m uvicorn app.api:app --host 127.0.0.1 --port 28472 --log-level warning
