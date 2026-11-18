@echo off
title co-company stop
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
