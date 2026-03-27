@echo off
REM ─────────────────────────────────────────────────────────────
REM Build do aplicativo Dados Básicos para Windows
REM Gera: dist\Dados Basicos AGU.exe
REM ─────────────────────────────────────────────────────────────

echo Instalando dependencias...
pip install requests selenium pyinstaller

echo.
echo Compilando aplicativo...
pyinstaller ^
  --onefile ^
  --noconsole ^
  --name "Dados Basicos AGU" ^
  --hidden-import selenium ^
  --hidden-import selenium.webdriver ^
  --hidden-import selenium.webdriver.chrome ^
  --hidden-import selenium.webdriver.chrome.options ^
  --hidden-import selenium.webdriver.chrome.service ^
  --hidden-import selenium.common.exceptions ^
  --hidden-import requests ^
  --hidden-import requests.adapters ^
  --hidden-import urllib3 ^
  app_dados_basicos.py

if %errorlevel% neq 0 (
  echo.
  echo ERRO no build. Verifique as mensagens acima.
  pause
  exit /b %errorlevel%
)

echo.
echo ============================================================
echo  Pronto! Executavel gerado em:
echo    dist\Dados Basicos AGU.exe
echo  Envie apenas esse arquivo .exe ao usuario.
echo ============================================================
pause
