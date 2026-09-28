@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 즉시 복기
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo [1/2] 마감된 예측 채점 + 틀린 날 복기 (15:40 이후면 오늘 것도 채점)
python cli.py verify
echo.
echo [2/2] 오늘 예측 vs 오늘 실제 움직임 관찰 복기 (AI 1회, 정식 채점 아님)
python cli.py review-now
echo.
echo 교훈은 lessons.json 에 저장되고 다음 분석의 중재자·트레이더에게 전달됩니다.
pause
