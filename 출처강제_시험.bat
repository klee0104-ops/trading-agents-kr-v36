@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 출처 표기 실전 시험
set PYTHONIOENCODING=utf-8
call venv\Scripts\activate.bat
echo 실제 데이터와 Gemini로 분석가 4명과 Bull/Bear 1라운드만 돌려 출처 표기 폐기율을 잽니다.
echo AI 호출 최대 8회. 예측 기록/모의계좌/공유 페이지는 바꾸지 않습니다.
if not exist logs mkdir logs
python -m tools.citation_trial > logs\citation_trial_last.txt 2>&1
echo 끝났습니다. 결과는 아래와 같고 logs 폴더에도 저장됐습니다. 이 창은 닫아도 됩니다.
echo.
chcp 65001 >nul
type logs\citation_trial_last.txt
pause
