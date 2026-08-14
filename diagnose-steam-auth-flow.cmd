@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo Steam authentication flow diagnostic
echo.
echo Close AetherSwap first and use the same network intended for normal operation.
echo This performs no trade, inventory, purchase, sale, or account modification.
echo It sends at most five read-only Steam GET requests and never logs cookie values or JWT queries.
echo.
choice /C YN /N /M "Continue? [Y/N]: "
if errorlevel 2 goto :end

".venv\Scripts\python.exe" "diagnostics\steam_auth_flow_probe.py"
echo.

:end
pause
endlocal
