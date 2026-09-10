@echo off
chcp 65001 >nul
title VoxDub Studio - Auto Reup Pipeline

cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"

if not defined PY (
    echo.
    echo  [LOI] Khong tim thay Python. Hay chay cai_dat.bat truoc.
    echo.
    pause
    exit /b 1
)

"%PY%" scripts\run_auto_reup.py %*
if errorlevel 1 (
    echo.
    echo  [Thong bao] Tien trinh da dung.
)
echo.
pause
