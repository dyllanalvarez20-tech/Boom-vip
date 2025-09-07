FROM ubuntu:22.04

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    wget \
    winetricks \
    wine \
    xvfb \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Configurar Wine para MT5
ENV WINEPREFIX=/opt/mt5
ENV WINEARCH=win64
ENV DISPLAY=:0

# Crear directorio para MT5
RUN mkdir -p /opt/mt5

# Descargar e instalar MT5
RUN wget -O /tmp/mt5-setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe && \
    xvfb-run wine /tmp/mt5-setup.exe /S && \
    rm /tmp/mt5-setup.exe

# Copiar archivos de la aplicación
WORKDIR /app
COPY . .

# Instalar dependencias de Python
RUN pip3 install -r requirements.txt

# Exponer puerto
EXPOSE 10000

# Comando de inicio
CMD ["./start_mt5.sh"]