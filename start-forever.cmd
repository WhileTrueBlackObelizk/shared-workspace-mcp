@echo off
cd /d "%~dp0"

REM ponytail: user-level restart loop; use a real Windows service if this must run before login.
:again
powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet) { exit 0 } else { exit 1 }" >nul 2>nul
if %errorlevel%==0 (
  timeout /t 10 /nobreak >nul
  goto again
)
call "%~dp0start.bat"
timeout /t 5 /nobreak >nul
goto again
