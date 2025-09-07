#!/bin/bash
echo "🚀 Iniciando construcción del bot BOOM1000 con MT5..."

# Instalar Wine y dependencias
sudo apt-get update
sudo apt-get install -y wine winetricks xvfb

# Configurar Wine
export WINEPREFIX=/opt/mt5
export WINEARCH=win64
export DISPLAY=:0

# Crear directorio para MT5
sudo mkdir -p /opt/mt5

# Descargar e instalar MT5
wget -O /tmp/mt5-setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
xvfb-run wine /tmp/mt5-setup.exe /S
rm /tmp/mt5-setup.exe

# Instalar dependencias de Python
pip install -r requirements.txt

echo "✅ Construcción completada"