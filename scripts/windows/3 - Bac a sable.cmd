@echo off
REM Bac a sable : copie de la base, rien n affecte tes vraies donnees (http://localhost:8502).
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\sandbox.ps1"
pause
