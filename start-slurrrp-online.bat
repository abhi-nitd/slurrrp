@echo off
title slurrrp ONLINE
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-online.ps1"
echo.
echo slurrrp is offline (this window closed the link).
pause >nul
