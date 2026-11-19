@echo off
REM Starts the bot under the watchdog (auto-restart). Double-click or use in Task Scheduler.
cd /d %~dp0
call .venv\Scripts\activate.bat
python watchdog\watchdog.py
