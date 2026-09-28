@echo off
setlocal
cd /d "%~dp0"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
python tools\quant_check.py
pause
