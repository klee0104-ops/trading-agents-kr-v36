@echo off
setlocal
cd /d "%~dp0"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
python cli.py verify >> "logs\마감채점_%TODAY%.log" 2>&1
python cli.py review-now >> "logs\마감채점_%TODAY%.log" 2>&1
python cli.py export-site >> "logs\마감채점_%TODAY%.log" 2>&1
rem 읽기 전용 페이지를 날짜별로 보관 (site\archive\index_YYYYMMDD.html)
if not exist site\archive mkdir site\archive
copy /y site\index.html "site\archive\index_%TODAY%.html" >nul
