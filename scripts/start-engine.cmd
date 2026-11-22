@echo off
setlocal
cd /d "%~dp0..\engine"
if not exist ".venv\Scripts\python.exe" (
  echo Create the venv first: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
".venv\Scripts\python.exe" main.py
