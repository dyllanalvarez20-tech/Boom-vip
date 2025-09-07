# BOOM1000 Trading Bot con MT5

Bot de trading automatizado para el índice BOOM1000 que ejecuta operaciones en MetaTrader 5.

## Características

- Análisis de velas en tiempo real
- Estrategia basada en EMA, RSI y ATR
- Ejecución automática en MT5
- Notificaciones por Telegram
- Auto-reconexión y auto-ping

## Configuración

1. Clona este repositorio
2. Configura las variables de entorno en Render.com:
   - `DERIV_TOKEN`: Tu token de Deriv
   - `TELEGRAM_TOKEN`: Token de tu bot de Telegram
   - `TELEGRAM_CHAT_ID`: ID del chat de Telegram
   - `MT5_LOGIN`: Tu número de cuenta MT5
   - `MT5_PASSWORD`: Tu contraseña MT5
   - `MT5_SERVER`: Servidor de tu broker MT5
   - `MT5_LOT_SIZE`: Tamaño del lote (ej: 0.1)

3. Despliega en Render.com

## Endpoints

- `/`: Estado del bot
- `/health`: Health check
- `/signals`: Historial de señales
- `/trades`: Historial de operaciones
- `/mt5/status`: Estado de MT5
- `/mt5/positions`: Posiciones abiertas
- `/mt5/close_all`: Cerrar todas las posiciones
- `/reconnect`: Forzar reconexión