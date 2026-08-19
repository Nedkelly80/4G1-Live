@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ============================================================
rem  4G1 Live AI launcher  (32-bit Python required for J2534)
rem  Uses launch.ps1 so failures are not silent under pythonw.
rem ============================================================

set "LOGDIR=%LOCALAPPDATA%\4G1 Live AI\Logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem Optional secrets (app also loads these itself)
set "SECRETS=%LOCALAPPDATA%\4G1 Live AI\secrets.env"
if exist "%SECRETS%" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%SECRETS%") do (
    if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
  )
)

if not exist "%~dp0app.py" (
  echo [%date% %time%] Missing app.py >> "%LOGDIR%\launch-crash.log"
  echo Missing app.py in %~dp0
  pause
  exit /b 1
)

if not exist "%~dp0launch.ps1" (
  echo [%date% %time%] Missing launch.ps1 >> "%LOGDIR%\launch-crash.log"
  echo Missing launch.ps1 — reinstall from the 4G1-Live-AI folder.
  pause
  exit /b 1
)

rem -WindowStyle Hidden keeps the flash of PowerShell brief; errors still MessageBox
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch.ps1"
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo [%date% %time%] launch.ps1 exit %EC% >> "%LOGDIR%\launch-crash.log"
)
exit /b %EC%
