@echo off
setlocal
cd /d "%~dp0.."
if not exist "bin" mkdir bin
set ZIP=%TEMP%\subparty-ffmpeg.zip
set TMP=%TEMP%\subparty-ffmpeg-extract
echo Downloading ffmpeg essentials...
curl.exe -L --retry 3 --retry-delay 2 -o "%ZIP%" "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if errorlevel 1 (
  echo Download failed.
  exit /b 1
)
if exist "%TMP%" rmdir /s /q "%TMP%"
mkdir "%TMP%"
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%TMP%' -Force"
for /r "%TMP%" %%F in (ffmpeg.exe) do copy /y "%%F" "bin\ffmpeg.exe" >nul
for /r "%TMP%" %%F in (ffprobe.exe) do copy /y "%%F" "bin\ffprobe.exe" >nul
del /q "%ZIP%" >nul 2>&1
rmdir /s /q "%TMP%" >nul 2>&1
if not exist "bin\ffmpeg.exe" (
  echo Could not find ffmpeg.exe in archive.
  exit /b 1
)
bin\ffmpeg.exe -version | more +0
echo OK: bin\ffmpeg.exe and bin\ffprobe.exe
