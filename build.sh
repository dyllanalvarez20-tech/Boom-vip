#!/bin/bash
echo "🚀 Iniciando construcción del bot BOOM1000 con MT5..."

# Instalar dependencias del sistema
apt-get update
apt-get install -y wine winetricks xvfb xauth  # Añade xauth

# Configurar Wine
export WINEPREFIX=/opt/mt5
export WINEARCH=win64
export DISPLAY=:0

# Crear directorio para MT5
mkdir -p /opt/mt5

# Configurar Xauth
export XAUTHORITY=/tmp/.Xauthority
touch $XAUTHORITY
xauth generate :0 . trusted

# Descargar e instalar MT5 (solo si hay credenciales)
if [ -n "$MT5_LOGIN" ] && [ -n "$MT5_PASSWORD" ] && [ -n "$MT5_SERVER" ]; then
    echo "📦 Instalando MT5..."
    wget -O /tmp/mt5-setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
    xvfb-run wine /tmp/mt5-setup.exe /S
    rm /tmp/mt5-setup.exe
else
    echo "⚠️  Credenciales MT5 no configuradas - Saltando instalación"
fi

# Instalar dependencias de Python
pip install --no-cache-dir -r requirements.txt

echo "✅ Construcción completada"
