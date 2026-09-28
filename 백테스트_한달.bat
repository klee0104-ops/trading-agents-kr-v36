@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 백테스트 (한 달) - 창을 닫으면 중단, 다시 실행하면 이어서 진행
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
rem 무료 요금제 분당 요청 제한 대비: AI 호출 사이 최소 5초 간격
set GEMINI_MIN_INTERVAL_SEC=5
echo 백테스트를 시작합니다. 거래일 하루당 1~2분, 전체 수십 분 걸릴 수 있습니다.
echo 진행 상황은 backtest_log.txt, 결과는 backtest_results.json 에 저장됩니다.
echo.
python cli.py backtest-save 2026-08-24 2026-09-26
echo.
echo 완료. (중간에 한도에 걸렸다면 나중에 이 파일을 다시 실행하면 이어서 진행합니다)
pause
