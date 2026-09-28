@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 백테스트 v2 (규칙엔진 조정 + SOX 가중) - 창을 닫으면 중단, 재실행 시 이어서 진행
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
set GEMINI_MIN_INTERVAL_SEC=5
echo v1(backtest_results.json)은 그대로 두고, 같은 기간을 v2로 다시 돌립니다.
echo 진행: backtest_results_v2_log.txt / 결과: backtest_results_v2.json
echo.
python cli.py backtest-save 2026-08-24 2026-09-26 ./backtest_results_v2.json "v2 (규칙엔진 조정+SOX)"
echo.
pause
