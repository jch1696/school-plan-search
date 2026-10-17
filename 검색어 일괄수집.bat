@echo off
cd /d "%~dp0"
title Collect
echo keywords.txt의 검색어로 최근 30일 문서를 수집합니다...
echo.
set PYTHONIOENCODING=utf-8
python collect.py 30
echo.
echo 끝났습니다. 아무 키나 누르면 닫힙니다.
pause >nul
