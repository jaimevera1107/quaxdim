@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Ejecutar_Escala_1_5_10M.ps1"
pause
