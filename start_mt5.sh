#!/bin/bash
echo "🚀 Iniciando MT5 y bot BOOM1000..."

# Configurar variables de entorno
export WINEPREFIX=/opt/mt5
export WINEARCH=win64
export DISPLAY=:0

# Iniciar MT5 en segundo plano
xvfb-run wine /opt/mt5/drive_c/Program\ Files/MetaTrader\ 5/terminal64.exe /portable &
sleep 30

# Iniciar la aplicación Flask
gunicorn app:app -b 0.0.0.0:10000 --timeout 120