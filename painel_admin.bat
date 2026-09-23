@echo off
title Trading Lab - Painel de Licencas
cd /d "%~dp0"
echo ======================================================================
echo    TRADING LAB DESKTOP - PAINEL ADMINISTRATIVO DE LICENCAS
echo ======================================================================
echo.
echo Iniciando servidor local e abrindo o painel no navegador...
echo Para encerrar o painel, feche esta janela ou pressione Ctrl+C.
echo.
C:\tlvenv\Scripts\python.exe scripts\admin_panel.py
echo.
pause
