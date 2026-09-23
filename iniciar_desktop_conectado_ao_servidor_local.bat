@echo off
title Trading Lab Desktop - Conectado ao Servidor Local
cd /d "c:\Users\Paulo R Advocacia\Documents\Codex\2026-08-23\referenced-chatgpt-conversation-this-is-an\work\trading-lab-desktop"
echo Conectando Trading Lab Desktop ao servidor local (http://localhost:8000)...
start "" "dist\TradingLab-Desktop-v1.9.11.exe" --auth-base-url http://localhost:8000
