#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Build do aplicativo Dados Básicos para macOS
# Gera: dist/Dados Básicos AGU.app
# ─────────────────────────────────────────────────────────────
set -e

echo "📦 Instalando dependências..."
pip3 install customtkinter requests selenium pyinstaller

echo "🔨 Compilando aplicativo..."
pyinstaller \
  --onefile \
  --windowed \
  --name "Dados Básicos AGU" \
  --hidden-import customtkinter \
  --hidden-import selenium \
  --hidden-import selenium.webdriver.chrome.options \
  --hidden-import selenium.webdriver.chrome.service \
  --hidden-import selenium.common.exceptions \
  --hidden-import requests \
  --collect-all customtkinter \
  app_dados_basicos.py

echo ""
echo "✅ Pronto! O aplicativo está em: dist/Dados Básicos AGU.app"
echo "   Copie-o para a pasta Aplicativos ou envie ao usuário."
