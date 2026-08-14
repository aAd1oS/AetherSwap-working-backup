@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0upload-safe-backup-to-github.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
  echo Finished successfully.
) else (
  echo Failed with exit code %EXIT_CODE%.
)
echo Press any key to close this window.
pause >nul
endlocal & exit /b %EXIT_CODE%
