@echo off
setlocal
cd /d "%~dp0"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
python cli.py export-site
echo.
echo site\index.html 을 다른 사람에게 보내거나 열어서 보여주면 됩니다. (실행 버튼 없음)
start "" "site\index.html"
pause
