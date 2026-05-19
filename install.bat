@echo off
echo ============================================
echo   OneClickBackup - Installation & Launcher
echo ============================================
echo.

REM Check for Python installation
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [OK] Python found:
python --version
echo.

REM Install requirements
echo Installing dependencies...
echo.
pip install -r "%~dp0requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo Try running this script as Administrator.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] All dependencies installed successfully.
echo.

REM Launch the application
echo Launching OneClickBackup...
echo.
python "%~dp0main.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Application exited with an error.
    echo.
    pause
    exit /b 1
)

exit /b 0
