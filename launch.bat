@echo off
chcp 65001 >nul 2>&1
title OneClick Backup ^& Disk Manager
color 0B
cd /d "%~dp0"

echo.
echo  ========================================
echo    OneClick Backup ^& Disk Manager v1.0
echo  ========================================
echo.

:: -----------------------------------------------------------
:: If we were re-launched with --elevated, skip admin check
:: -----------------------------------------------------------
if "%~1"=="--elevated" (
    echo  [OK] Relance en mode administrateur.
    goto :start
)

:: -----------------------------------------------------------
:: Check admin rights (first launch only)
:: -----------------------------------------------------------
net session >nul 2>&1
if %errorlevel% equ 0 (
    echo  [OK] Droits administrateur actifs.
    goto :start
)

echo  [!] Pas de droits administrateur.
echo      Relancement en mode administrateur...
echo.
powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs"
exit /b

:: -----------------------------------------------------------
:: Main application start
:: -----------------------------------------------------------
:start
echo  [OK] Dossier: %cd%
echo.

:: Detect Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERREUR] Python introuvable dans le PATH.
    echo           Installez Python 3.10+ depuis https://python.org
    goto :fin
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] %PYVER%

:: Install dependencies if needed
python -c "import customtkinter, psutil" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [~] Installation des dependances...
    pip install -r requirements.txt --quiet
    if %errorlevel% neq 0 (
        echo  [ERREUR] pip install a echoue.
        goto :fin
    )
    echo  [OK] Dependances installees.
)

:: Launch
echo.
echo  Lancement de l'application...
echo  ----------------------------------------
echo.
python main.py
set APP_EXIT=%errorlevel%
echo.

if %APP_EXIT% neq 0 (
    echo  [ERREUR] Code de sortie: %APP_EXIT%
) else (
    echo  Application fermee.
)

:fin
echo.
echo  Appuyez sur une touche pour fermer...
pause >nul
