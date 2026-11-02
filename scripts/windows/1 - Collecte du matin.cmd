@echo off
REM Collecte du matin + file de revue. Autorise le script pour cette execution seulement.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\morning_review.ps1"
echo.
pause
