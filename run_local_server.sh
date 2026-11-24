#!/bin/bash
# Worksite GIS — macOS 本機伺服器啟動腳本
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
  echo "[錯誤] 找不到虛擬環境 (.venv/bin/python)"
  exit 1
fi

echo "========================================="
echo "  啟動 Worksite GIS 伺服器"
echo "  網址: http://127.0.0.1:8000/map/"
echo "  按 Ctrl+C 可停止伺服器"
echo "========================================="

exec .venv/bin/python manage.py runserver 127.0.0.1:8000
