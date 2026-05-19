@echo off
chcp 65001 >nul 2>&1
title OneClick Backup — Dependency Installer
color 0B
cd /d "%~dp0"

echo.
echo  ========================================
echo    OneClick Backup — Dependency Installer
echo  ========================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERREUR] Python introuvable dans le PATH.
    echo           Installez Python 3.10+ depuis https://python.org
    echo           Cochez "Add Python to PATH" pendant l'installation.
    goto :fin
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] %PYVER%

:: Check pip
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERREUR] pip introuvable.
    echo           Reinstallez Python avec pip inclus.
    goto :fin
)

:: Install dependencies
echo.
echo  Installation des dependances...
echo  ----------------------------------------
echo.
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo  [ERREUR] pip install a echoue.
    echo           Verifiez votre connexion internet
    echo           ou lancez en tant qu'administrateur.
    goto :fin
)

echo.
echo  ========================================
echo    [OK] Installation terminee !
echo  ========================================
echo.
echo  Pour lancer l'application:
echo    - Double-cliquez sur launch.bat
echo    - Ou executez: python main.py
echo.

:fin
echo  Appuyez sur une touche pour fermer...
pause >nul
