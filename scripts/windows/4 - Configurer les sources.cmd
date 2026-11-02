@echo off
REM Assistant de configuration des sources (Google Jobs, Jooble, Gmail...).
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\configurer_sources.ps1"
pause
