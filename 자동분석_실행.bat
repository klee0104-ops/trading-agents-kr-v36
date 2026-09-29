@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
"%~dp0venv\Scripts\python.exe" -B -m tools.morning_run
exit /b %errorlevel%
