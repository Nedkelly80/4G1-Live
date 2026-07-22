@echo off
setlocal
cd /d "%~dp0"

rem 4G1 Live must run on 32-bit Python: the J2534 adapter driver is 32-bit.
rem Prefer the Python launcher, then fall back to a per-user 3.13 install.

where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw -3-32 app.py
    exit /b
)

set "PYW=%LOCALAPPDATA%\Programs\Python\Python313-32\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" app.py
    exit /b
)

echo.
echo 32-bit Python 3 was not found.
echo 4G1 Live requires 32-bit Python because the J2534 adapter driver is 32-bit.
echo Install it from python.org, choosing the 32-bit ("x86") installer.
echo.
pause
