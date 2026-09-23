@echo off
title Trading Lab - Servidor de Licencas e Painel Admin
cd /d "$PSScriptRoot"
cd /d "c:\Users\Paulo R Advocacia\Documents\Codex\2026-08-23\referenced-chatgpt-conversation-this-is-an\work\trading-lab-desktop"
echo ============================================================
echo Iniciando Servidor de Licencas Trading Lab na porta 8000...
echo Acesse o Painel Admin no navegador: http://localhost:8000/admin
echo E-mail do Admin: admin@tradinglab.app
echo O codigo OTP de login aparecera aqui nesta janela.
echo ============================================================
echo.
C:\tlvenv\Scripts\python.exe -m uvicorn apps.license_server.main:app --port 8000 --reload
pause
