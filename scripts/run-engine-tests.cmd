@echo off
setlocal
cd /d "%~dp0..\engine"
if not exist ".venv\Scripts\python.exe" (
  echo Create the engine venv first: python -m venv .venv ^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
echo Running SubParty engine test suite...
".venv\Scripts\python.exe" -m unittest discover -s tests -v
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
  echo FAILED
  exit /b %ERR%
)
echo OK
exit /b 0
