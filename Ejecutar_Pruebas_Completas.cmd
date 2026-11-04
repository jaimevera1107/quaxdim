@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Ejecutar_Pruebas.ps1" -Modo completo %*
echo.
echo Revisa el directorio reportes para los informes y resultados.
pause
