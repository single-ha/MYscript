@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先用项目内 venv 的 Python（依赖齐全，启动即用）；没有 venv 再回退系统 PATH。
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" start.py
  exit /b
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3.10+ and check "Add to PATH".
  pause
  exit /b
)
python start.py
