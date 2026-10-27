@echo off
cd /d "%~dp0"

python -c "import sys; assert sys.version_info >= (3, 10) and sys.maxsize > 2**32; import PyQt6.QtWidgets" >nul 2>&1
if not errorlevel 1 (
    python "%~dp0gpu_manager.py" %*
    goto :finished
)

where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3 3.14 3.13 3.12 3.11 3.10) do (
        py -%%V -c "import sys; assert sys.version_info >= (3, 10) and sys.maxsize > 2**32; import PyQt6.QtWidgets" >nul 2>&1
        if not errorlevel 1 (
            py -%%V "%~dp0gpu_manager.py" %*
            goto :finished
        )
    )
)

echo Could not find a 64-bit Python 3.10 or newer with PyQt6 installed.
echo Install PyQt6 for the Python version you want to use, then try again.
pause
exit /b 1

:finished
if errorlevel 1 pause
