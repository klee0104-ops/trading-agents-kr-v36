@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 진단 (API 과금 없음)
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo 진단 중입니다...
> 진단결과.txt 2>&1 (
  echo ==== key-status ====
  python cli.py key-status
  echo ==== 거래일 판정 / 시세 조회 ====
  python tools\session_check.py
  echo ==== LLM 클라이언트 생성 - 호출 안 함 ====
  python -c "import cli; c=cli._real_llm(); print('OK', c)"
)
type 진단결과.txt
echo.
pause
