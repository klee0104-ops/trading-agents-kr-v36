@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 어제 관찰 복기
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo 직전 거래일의 실제 KOSPI 움직임과 그날 참고한 예측을 대조해 교훈을 남깁니다. (AI 1회, 정식 채점 아님)
python cli.py review-now yesterday
echo.
pause
