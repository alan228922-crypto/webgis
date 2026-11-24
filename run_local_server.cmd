@echo off
setlocal
cd /d "%~dp0"
title WORKSITE Django Local Server

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment was not found.
  pause
  exit /b 1
)

echo Starting WORKSITE at http://127.0.0.1:8000/map/
echo Keep this window open while using the website.
".venv\Scripts\python.exe" -u manage.py runserver 127.0.0.1:8000 --noreload

echo.
echo The WORKSITE server has stopped.
pause
