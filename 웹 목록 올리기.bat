@echo off
cd /d "%~dp0"
title Upload list for phone
echo 웹에서 모은 목록을 받아 PC와 합치고, 다시 GitHub에 올립니다.
echo.
git pull --rebase
if errorlevel 1 goto fail
python export_web.py
if errorlevel 1 goto fail
git add docs/data.json
git commit -m "목록 갱신" >nul 2>&1
if errorlevel 1 (
  echo 바뀐 내용이 없습니다.
) else (
  git push
  if errorlevel 1 goto fail
  echo.
  echo 올렸습니다. 1~2분 뒤 웹에서 새로고침하면 반영됩니다.
)
pause
exit /b 0
:fail
echo.
echo 실패했습니다. 위 메시지를 확인해 주세요.
pause
