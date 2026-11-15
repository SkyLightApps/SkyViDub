@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "dub\gui.py"
if errorlevel 1 pause
