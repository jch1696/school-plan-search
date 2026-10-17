@echo off
cd /d "%~dp0"
title School Plan Finder
echo 학교 계획서 검색기를 시작합니다. 잠시 후 브라우저가 열립니다.
echo 이 창을 닫으면 프로그램이 꺼집니다.
echo.
python server.py
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. Python이 설치되어 있는지 확인해 주세요.
  pause
)
