@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python "%~dp0gpu_manager.py"
) else (
    py -3 "%~dp0gpu_manager.py"
)
if errorlevel 1 pause
