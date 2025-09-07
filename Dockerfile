FROM ubuntu:22.04

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    wget \
    winetricks \
    wine \
    xvfb \
    xauth \  # ¡AÑADE ESTO!
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Configurar Wine para MT5
ENV WINEPREFIX=/opt/mt5
ENV WINEARCH=win64
ENV DISPLAY=:0

# Crear directorio para MT5
RUN mkdir -p /opt/mt5

# Crear directorio de la aplicación
WORKDIR /app
COPY . .

# Instalar dependencias de Python
RUN pip3 install --no-cache-dir -r requirements.txt

# Hacer los scripts ejecutables
RUN chmod +x build.sh start_mt5.sh

# Exponer puerto
EXPOSE 10000

# Comando de inicio
CMD ["./start_mt5.sh"]
