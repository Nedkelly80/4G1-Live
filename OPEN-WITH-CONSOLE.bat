@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 4G1 Live AI (debug console)
echo ============================================================
echo  4G1 Live AI — debug console
echo  Errors stay on this screen. Close the window when done.
echo ============================================================
echo.

set "PY=%LOCALAPPDATA%\Programs\Python\Python313-32\python.exe"
if not exist "%PY%" (
  echo Missing 32-bit Python:
  echo   %PY%
  echo.
  echo Install Python 3.13 x86 from https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0app.py" (
  echo Missing app.py in:
  echo   %~dp0
  pause
  exit /b 1
)

echo Using: %PY%
echo App:   %~dp0app.py
echo.
"%PY%" "%~dp0app.py"
echo.
echo Exit code: %ERRORLEVEL%
if errorlevel 1 (
  echo.
  echo App exited with an error. Logs:
  echo   %LOCALAPPDATA%\4G1 Live AI\Logs
  echo.
)
pause
