#!/bin/bash
echo "🚀 Iniciando MT5 y bot BOOM1000..."

# Configurar variables de entorno
export WINEPREFIX=/opt/mt5
export WINEARCH=win64
export DISPLAY=:0

# Configurar Xauth (importante para Xvfb)
export XAUTHORITY=/tmp/.Xauthority
touch $XAUTHORITY
xauth generate :0 . trusted

# Instalar gunicorn si no está presente
if ! command -v gunicorn &> /dev/null; then
    echo "📦 Instalando gunicorn..."
    pip3 install gunicorn
fi

# Iniciar MT5 en segundo plano (solo si hay credenciales)
if [ -n "$MT5_LOGIN" ] && [ -n "$MT5_PASSWORD" ] && [ -n "$MT5_SERVER" ]; then
    echo "🔧 Iniciando MT5..."
    xvfb-run wine /opt/mt5/drive_c/Program\ Files/MetaTrader\ 5/terminal64.exe /portable &
    sleep 30
else
    echo "⚠️  Credenciales MT5 no configuradas - Modo análisis solamente"
fi

# Iniciar la aplicación Flask
echo "🌐 Iniciando servidor web..."
gunicorn app:app -b 0.0.0.0:10000 --timeout 120 --access-logfile -
