@echo off
setlocal enabledelayedexpansion
set "ADBEXE=adb"
where adb >nul 2>nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
    set "ADBEXE=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
  ) else (
    echo ADB not found. Install Android Platform Tools.
    pause
    exit /b 1
  )
)
echo Using: !ADBEXE!
echo Checking connected devices...
"!ADBEXE!" devices
"!ADBEXE!" forward tcp:8080 tcp:8080
if errorlevel 1 (
  echo Forward failed. Enable USB debugging and authorize this PC.
  pause
  exit /b 1
)
echo.
echo OK: phone port 8080 forwarded to this PC.
echo Start the IP Webcam server on your phone, then launch the app.
echo Keep this window open while using the phone camera.
pause
