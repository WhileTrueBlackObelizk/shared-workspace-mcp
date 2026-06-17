@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1

if "%~1"=="--setup-only" exit /b 0

echo.
echo  Shared Workspace MCP Server
echo  URL: http://localhost:8765/sse
echo  Stoppen: Strg+C
echo.

".venv\Scripts\python.exe" server.py
