@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 백테스트 v3 (확률+거시지표+중재자+분석가4명) - 창을 닫으면 중단, 재실행 시 이어서 진행
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
set GEMINI_MIN_INTERVAL_SEC=5
echo v1/v2는 그대로 두고, 같은 기간을 v3로 돌립니다 (무료 한도에 걸리면 자동 중단, 재실행 시 이어서).
python cli.py backtest-save 2026-08-24 2026-09-26 ./backtest_results_v3.json "v3 (확률+거시+중재자+분석가4)"
echo.
pause
