@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR V3.6 - Mock 데모

rem 예전 버전에서 패키지 설치가 잘 됐던 Python 3.14를 우선 사용
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

if exist "venv\.ta_ready" goto ready
echo [최초 실행] 가상환경을 만들고 패키지를 설치합니다. 몇 분 걸릴 수 있습니다...
"%PY%" -m venv --clear venv
if errorlevel 1 goto fail
call venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
echo ok> venv\.ta_ready

:ready
call venv\Scripts\activate.bat
echo.
echo ================= Mock 데모 (API 키 불필요, 과금 없음) =================
python cli.py demo
echo.
echo ================= API 키 상태 =================
python cli.py key-status
echo.
echo 메뉴 화면으로 계속하려면 start.bat을 실행하세요.
pause
exit /b 0

:fail
echo.
echo [오류] 설치에 실패했습니다. 위 메시지를 확인해주세요.
pause
exit /b 1
