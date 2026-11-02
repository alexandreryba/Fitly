@echo off
REM Ouvre l interface Fitly (http://localhost:8501). Ferme cette fenetre pour l arreter.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\start_ui.ps1"
pause
