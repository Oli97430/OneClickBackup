@echo off
chcp 65001 >nul 2>&1
title OneClick Backup ^& Disk Manager
color 0B

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║   OneClick Backup ^& Disk Manager  v1.0.0    ║
echo  ╚══════════════════════════════════════════════╝
echo.

:: -----------------------------------------------------------
:: Check admin rights
:: -----------------------------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Pas de droits administrateur.
    echo      Relancement en mode administrateur...
    echo.
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)
echo  [OK] Droits administrateur actifs.

:: -----------------------------------------------------------
:: Move to the script's directory
:: -----------------------------------------------------------
cd /d "%~dp0"

:: -----------------------------------------------------------
:: Detect Python
:: -----------------------------------------------------------
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERREUR] Python introuvable dans le PATH.
    echo           Installez Python 3.10+ depuis https://python.org
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] %PYVER%

:: -----------------------------------------------------------
:: Install dependencies if needed
:: -----------------------------------------------------------
python -c "import customtkinter, psutil" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [~] Installation des dependances...
    pip install -r requirements.txt --quiet
    if %errorlevel% neq 0 (
        echo  [ERREUR] pip install a echoue.
        pause
        exit /b 1
    )
    echo  [OK] Dependances installees.
)

:: -----------------------------------------------------------
:: Launch the application
:: -----------------------------------------------------------
echo.
echo  Lancement de l'application...
echo  ─────────────────────────────────────────────
echo.
python main.py

:: -----------------------------------------------------------
:: Always pause so the window stays open on exit/error
:: -----------------------------------------------------------
echo.
if %errorlevel% neq 0 (
    echo  [ERREUR] L'application s'est terminee avec le code %errorlevel%.
) else (
    echo  Application fermee normalement.
)
echo.
pause
