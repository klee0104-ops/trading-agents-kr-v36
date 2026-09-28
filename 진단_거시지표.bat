@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
python tools\news_check.py > 진단결과.txt 2>&1
timeout /t 2 >nul
