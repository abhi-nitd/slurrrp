@echo off
title slurrrp cart app
cd /d "%~dp0"
echo Starting slurrrp... open the "Phones (Wi-Fi)" URL below on each phone.
echo.
python server.py 8000
echo.
echo slurrrp has stopped. Press any key to close.
pause >nul
