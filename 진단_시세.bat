@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
python tools\diag_hist.py > logs\diag_hist.txt 2>&1
