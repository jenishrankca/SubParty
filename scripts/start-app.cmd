@echo off
setlocal
cd /d "%~dp0..\app"
if not exist "node_modules\electron" (
  echo Run npm install in app\ first.
  exit /b 1
)
call npm run dev
