@echo off
chcp 65001 > nul
title Generador de Actas Consensus - Servidor Local

echo ======================================================================
echo   CONSENSUS S.A.S. - Generador Inteligente de Actas (Modo Local)
echo ======================================================================
echo.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo [INFO] Utilizando entorno virtual local (.venv)...
    call ".venv\Scripts\activate.bat"
    python main.py
) else (
    echo [ADVERTENCIA] No se detecto .venv local. Utilizando Python del sistema...
    python main.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Ocurrio un error al ejecutar la aplicacion.
    pause
)
