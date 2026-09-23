@echo off
title Gerador de Licenca - Trading Lab Desktop
cd /d "%~dp0"
echo ======================================================================
echo    TRADING LAB DESKTOP - GERADOR DE LICENCA CRIPTOGRAFICA OFFLINE
echo ======================================================================
echo.
set /p CLIENTE="Digite o nome do cliente (ex: Cliente VIP): "
if "%CLIENTE%"=="" set CLIENTE=Cliente Trading Lab

set /p DIAS="Digite a validade em dias (ou pressione ENTER para 365 dias): "
if "%DIAS%"=="" set DIAS=365

echo.
echo Gerando licenca Pro (Modo Real + Demo)...
echo.
C:\tlvenv\Scripts\python.exe scripts\gerar_licenca.py --cliente "%CLIENTE%" --dias %DIAS%
echo.
pause
