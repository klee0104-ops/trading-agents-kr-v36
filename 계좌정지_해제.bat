@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 계좌 손실정지 해제
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo 손실 한도로 멈춘 모의 계좌를 검토 후 다시 운용합니다. 실제 돈은 쓰지 않습니다.
echo 계좌 이름: long_core, short, short_leverage, medium, long
echo.
set /p SLEEVE=해제할 계좌 이름: 
set /p REVIEWER=검토자 이름: 
set /p REASON=검토 사유 - 10자 이상: 
echo.
echo [1/2] 검토 기록
python tools\paper_portfolio.py --review-stop %SLEEVE% --reviewer "%REVIEWER%" --reason "%REASON%"
if errorlevel 1 goto fail
echo [2/2] 재개 - 현재 평가금액을 새 손실 기준점으로 잡습니다
python tools\paper_portfolio.py --resume-stop %SLEEVE% --reviewer "%REVIEWER%" --reason "%REASON%"
if errorlevel 1 goto fail
echo.
echo 해제 완료. 다음 시세 점검부터 다시 운용됩니다. 백업은 .review-backups 폴더에 있습니다.
pause
exit /b 0
:fail
echo.
echo 해제하지 못했습니다. 계좌 이름, 보유 종목 남음 여부, 사유 길이를 확인하세요.
echo 자세한 원인: logs\paper_portfolio_errors.log
pause
exit /b 1
