import MetaTrader5 as mt5
import websocket
import json
import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime
import ssl
from collections import deque
import requests
from flask import Flask, jsonify
import atexit
import os

app = Flask(__name__)

class BOOM1000CandleAnalyzer:
    def __init__(self, token, app_id="88258", telegram_token=None, telegram_chat_id=None,
                 mt5_login=None, mt5_password=None, mt5_server=None, mt5_lot_size=0.1):
        # --- Configuración de Conexión ---
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.token = token
        self.ws = None
        self.connected = False
        self.authenticated = False
        self.last_reconnect_time = time.time()
        self.service_url = "https://boom-vip.onrender.com"

        # --- Configuración de Telegram ---
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.telegram_enabled = telegram_token is not None and telegram_chat_id is not None

        # --- Configuración de MT5 ---
        self.mt5_login = mt5_login
        self.mt5_password = mt5_password
        self.mt5_server = mt5_server
        self.mt5_lot_size = mt5_lot_size
        self.mt5_connected = False
        self.mt5_symbol = "BOOM1000"  # Asegúrate que este símbolo existe en tu MT5

        # --- Configuración de Trading ---
        self.symbol = "BOOM1000"
        self.candle_interval_seconds = 60
        self.min_candles = 1

        # --- Parámetros de la Estrategia ---
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.ema_trend_period = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.sl_atr_multiplier = 2.0
        self.tp_atr_multiplier = 3.0

        # --- Almacenamiento de Datos ---
        self.ticks_for_current_candle = []
        self.candles = deque(maxlen=200)
        self.last_candle_timestamp = 0
        self.new_candle_ready = False

        # --- Estado de Señales ---
        self.last_signal_time = 0
        self.signal_cooldown = self.candle_interval_seconds * 2
        self.last_signal = None
        self.signals_history = []
        self.trades_history = []

        # Inicializar MT5
        self.init_mt5()

        # Iniciar en un hilo separado
        self.thread = threading.Thread(target=self.run_analyzer, daemon=True)
        self.thread.start()

    def self_ping(self):
        """Función para hacerse ping a sí mismo y evitar que Render duerma el servicio"""
        try:
            health_url = f"{self.service_url}/health"
            response = requests.get(health_url, timeout=10)
            print(f"✅ Self-ping exitoso: {response.status_code}")
            return True
        except Exception as e:
            print(f"❌ Error en self-ping: {e}")
            return False

    def init_mt5(self):
        """Inicializa la conexión con MetaTrader 5"""
        if not all([self.mt5_login, self.mt5_password, self.mt5_server]):
            print("⚠️  Credenciales MT5 no configuradas - Solo modo análisis")
            return False
        
        try:
            # Ruta donde está instalado MT5 (ajusta según tu instalación)
            mt5_path = "/opt/mt5/terminal64.exe"
            
            if not os.path.exists(mt5_path):
                print("❌ MT5 no está instalado en la ruta esperada")
                return False
                
            if not mt5.initialize(path=mt5_path):
                print(f"❌ Error inicializando MT5: {mt5.last_error()}")
                return False
            
            # Intentar login
            authorized = mt5.login(self.mt5_login, self.mt5_password, self.mt5_server)
            if not authorized:
                print(f"❌ Error login MT5: {mt5.last_error()}")
                mt5.shutdown()
                return False
            
            self.mt5_connected = True
            print("✅ Conexión MT5 exitosa")
            
            # Verificar que el símbolo está disponible
            symbol_info = mt5.symbol_info(self.mt5_symbol)
            if symbol_info is None:
                print(f"❌ Símbolo {self.mt5_symbol} no encontrado en MT5")
                self.mt5_connected = False
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ Error inicializando MT5: {e}")
            return False

    def open_mt5_trade(self, direction, price, atr_value):
        """Abre una operación en MT5"""
        if not self.mt5_connected:
            print("❌ MT5 no conectado - No se puede abrir operación")
            return False
        
        try:
            # Calcular SL y TP basados en ATR
            if direction == "BUY":
                sl_price = price - (atr_value * self.sl_atr_multiplier)
                tp_price = price + (atr_value * self.tp_atr_multiplier)
                trade_type = mt5.ORDER_TYPE_BUY
            else:  # SELL
                sl_price = price + (atr_value * self.sl_atr_multiplier)
                tp_price = price - (atr_value * self.tp_atr_multiplier)
                trade_type = mt5.ORDER_TYPE_SELL

            # Preparar la solicitud de operación
            symbol = self.mt5_symbol
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                print(f"❌ Símbolo {symbol} no disponible")
                return False
            
            # Si el símbolo no está seleccionado, intentar seleccionarlo
            if not symbol_info.visible:
                if not mt5.symbol_select(symbol, True):
                    print(f"❌ No se pudo seleccionar el símbolo {symbol}")
                    return False
            
            point = mt5.symbol_info(symbol).point
            deviation = 20
            
            # Crear la solicitud
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": self.mt5_lot_size,
                "type": trade_type,
                "price": price,
                "sl": sl_price,
                "tp": tp_price,
                "deviation": deviation,
                "magic": 234000,
                "comment": "BOOM1000 Bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            
            # Enviar la orden
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"❌ Error abriendo operación: {result.retcode}")
                return False
            
            print(f"✅ Operación {direction} abierta exitosamente")
            print(f"   Ticket: {result.order}")
            print(f"   Precio: {result.price}")
            print(f"   SL: {sl_price}")
            print(f"   TP: {tp_price}")
            
            # Guardar en historial
            trade_info = {
                'ticket': result.order,
                'direction': direction,
                'price': result.price,
                'sl': sl_price,
                'tp': tp_price,
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'lot_size': self.mt5_lot_size
            }
            self.trades_history.append(trade_info)
            
            return True
            
        except Exception as e:
            print(f"❌ Error abriendo operación en MT5: {e}")
            return False

    def close_all_mt5_trades(self):
        """Cierra todas las operaciones abiertas"""
        if not self.mt5_connected:
            return False
        
        try:
            positions = mt5.positions_get()
            if positions is None:
                print("❌ Error obteniendo posiciones")
                return False
            
            if len(positions) == 0:
                print("✅ No hay posiciones abiertas")
                return True
            
            for position in positions:
                symbol = position.symbol
                volume = position.volume
                position_id = position.ticket
                
                if position.type == mt5.ORDER_TYPE_BUY:
                    order_type = mt5.ORDER_TYPE_SELL
                    price = mt5.symbol_info_tick(symbol).bid
                else:
                    order_type = mt5.ORDER_TYPE_BUY
                    price = mt5.symbol_info_tick(symbol).ask
                
                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volume,
                    "type": order_type,
                    "position": position_id,
                    "price": price,
                    "deviation": 20,
                    "magic": 234000,
                    "comment": "Cierre por bot",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_FOK,
                }
                
                result = mt5.order_send(close_request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    print(f"❌ Error cerrando posición {position_id}: {result.retcode}")
                else:
                    print(f"✅ Posición {position_id} cerrada")
            
            return True
            
        except Exception as e:
            print(f"❌ Error cerrando operaciones: {e}")
            return False

    # --- Métodos para calcular indicadores manualmente ---
    def calculate_ema(self, prices, period):
        """Calcula EMA manualmente"""
        if len(prices) < period:
            return np.array([np.nan] * len(prices))
        
        ema = np.zeros(len(prices))
        k = 2 / (period + 1)
        
        # Primer valor EMA es SMA simple
        ema[period-1] = np.mean(prices[:period])
        
        # Calcular EMA para los valores restantes
        for i in range(period, len(prices)):
            ema[i] = (prices[i] * k) + (ema[i-1] * (1 - k))
        
        return ema

    def calculate_rsi(self, prices, period=14):
        """Calcula RSI manualmente"""
        if len(prices) < period + 1:
            return np.array([np.nan] * len(prices))
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.zeros(len(prices))
        avg_loss = np.zeros(len(prices))
        rsi = np.zeros(len(prices))
        
        # Valores iniciales
        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])
        
        for i in range(period + 1, len(prices)):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gains[i-1]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + losses[i-1]) / period
        
        for i in range(period, len(prices)):
            if avg_loss[i] == 0:
                rsi[i] = 100
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100 - (100 / (1 + rs))
        
        return rsi

    def calculate_atr(self, highs, lows, closes, period=14):
        """Calcula ATR manualmente"""
        if len(highs) < period + 1:
            return np.array([np.nan] * len(highs))
        
        tr = np.zeros(len(highs))
        atr = np.zeros(len(highs))
        
        # Calcular True Range
        for i in range(1, len(highs)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr[i] = max(hl, hc, lc)
        
        # Primer ATR es el promedio simple de los primeros period TR
        atr[period] = np.mean(tr[1:period+1])
        
        # Calcular ATR para los valores restantes
        for i in range(period + 1, len(highs)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        
        return atr

    # --- Método para enviar mensajes a Telegram ---
    def send_telegram_message(self, message):
        if not self.telegram_enabled:
            print("❌ Telegram no está configurado. No se enviará mensaje.")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("✅ Señal enviada a Telegram")
                return True
            else:
                print(f"❌ Error al enviar a Telegram: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Excepción al enviar a Telegram: {e}")
            return False

    # --- Métodos de Conexión ---
    def connect(self):
        print("🌐 Conectando a Deriv API...")
        try:
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            wst = threading.Thread(target=self.ws.run_forever, kwargs={
                'sslopt': {"cert_reqs": ssl.CERT_NONE}, 'ping_interval': 30, 'ping_timeout': 10
            })
            wst.daemon = True
            wst.start()
            
            # Esperar a que se conecte
            timeout = 10
            start_time = time.time()
            while not self.connected and time.time() - start_time < timeout:
                time.sleep(0.1)
                
            return self.connected
        except Exception as e:
            print(f"❌ Error en conexión: {e}")
            return False

    def disconnect(self):
        """Cierra la conexión WebSocket"""
        if self.ws:
            self.ws.close()
            self.connected = False
            self.authenticated = False
            print("🔌 Conexión cerrada manualmente")

    def on_open(self, ws):
        print("✅ Conexión abierta")
        self.connected = True
        ws.send(json.dumps({"authorize": self.token}))

    def on_close(self, ws, close_status_code, close_msg):
        print("🔌 Conexión cerrada")
        self.connected = False
        self.authenticated = False

    def on_error(self, ws, error):
        print(f"❌ Error WebSocket: {error}")

    def on_message(self, ws, message):
        data = json.loads(message)
        if "error" in data:
            print(f"❌ Error: {data['error'].get('message', 'Error desconocido')}")
            return
        if "authorize" in data:
            self.authenticated = True
            print("✅ Autenticación exitosa.")
            self.subscribe_to_ticks()
        elif "tick" in data:
            self.handle_tick(data['tick'])

    def subscribe_to_ticks(self):
        print(f"📊 Suscribiendo a ticks de {self.symbol}...")
        self.ws.send(json.dumps({"ticks": self.symbol, "subscribe": 1}))
        print("⏳ Recopilando datos para formar la primera vela...")

    def handle_tick(self, tick):
        try:
            price = float(tick['quote'])
            timestamp = int(tick['epoch'])

            current_candle_start_time = timestamp - (timestamp % self.candle_interval_seconds)

            if self.last_candle_timestamp == 0:
                self.last_candle_timestamp = current_candle_start_time

            if current_candle_start_time > self.last_candle_timestamp:
                self._finalize_candle()
                self.last_candle_timestamp = current_candle_start_time

            self.ticks_for_current_candle.append(price)

        except Exception as e:
            print(f"❌ Error en handle_tick: {e}")

    def _finalize_candle(self):
        if not self.ticks_for_current_candle:
            return

        prices = np.array(self.ticks_for_current_candle)
        candle = {
            'timestamp': self.last_candle_timestamp,
            'open': prices[0],
            'high': np.max(prices),
            'low': np.min(prices),
            'close': prices[-1],
            'volume': len(prices)
        }
        self.candles.append(candle)
        self.ticks_for_current_candle = []
        self.new_candle_ready = True

        if len(self.candles) >= self.min_candles:
            print(f"🕯️ Nueva vela cerrada. Total: {len(self.candles)}. Precio Cierre: {candle['close']:.2f}")

    def analyze_market(self):
        if len(self.candles) < self.min_candles:
            print(f"\r⏳ Recopilando velas iniciales: {len(self.candles)}/{self.min_candles}", end="")
            return

        # Extraer arrays de numpy
        opens = np.array([c['open'] for c in self.candles], dtype=float)
        highs = np.array([c['high'] for c in self.candles], dtype=float)
        lows = np.array([c['low'] for c in self.candles], dtype=float)
        closes = np.array([c['close'] for c in self.candles], dtype=float)

        try:
            # Calcular indicadores manualmente
            ema_fast = self.calculate_ema(closes, self.ema_fast_period)
            ema_slow = self.calculate_ema(closes, self.ema_slow_period)
            ema_trend = self.calculate_ema(closes, self.ema_trend_period)
            rsi = self.calculate_rsi(closes, self.rsi_period)
            atr = self.calculate_atr(highs, lows, closes, self.atr_period)
        except Exception as e:
            print(f"❌ Error calculando indicadores: {e}")
            return

        # Verificar si tenemos suficientes datos para análisis
        if len(closes) < self.ema_trend_period or np.isnan(ema_fast[-1]) or np.isnan(ema_slow[-1]) or np.isnan(rsi[-1]) or np.isnan(atr[-1]):
            return

        last_close = closes[-1]
        last_atr = atr[-1]

        is_uptrend = ema_fast[-1] > ema_slow[-1] and ema_slow[-1] > ema_trend[-1]
        is_downtrend = ema_fast[-1] < ema_slow[-1] and ema_slow[-1] < ema_trend[-1]

        signal = None
        current_time = time.time()

        if current_time - self.last_signal_time < self.signal_cooldown:
            return

        # Señal de COMPRA (BUY)
        if is_uptrend and len(ema_fast) > 1 and ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            if rsi[-1] > 40 and rsi[-1] < 70:
                signal = "BUY"

        # Señal de VENTA (SELL)
        if is_downtrend and len(ema_fast) > 1 and ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            if rsi[-1] < 60 and rsi[-1] > 30:
                signal = "SELL"

        if signal:
            self.last_signal_time = current_time
            self.last_signal = {
                'direction': signal,
                'price': last_close,
                'atr': last_atr,
                'rsi': rsi[-1],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.signals_history.append(self.last_signal)
            
            self.display_signal(signal, last_close, last_atr, rsi[-1])

            # Enviar señal a Telegram
            if self.telegram_enabled:
                telegram_msg = self.format_telegram_message(signal, last_close, last_atr, rsi[-1])
                self.send_telegram_message(telegram_msg)

    def format_telegram_message(self, direction, price, atr_value, rsi_value):
        if direction == "BUY":
            sl = price - (atr_value * self.sl_atr_multiplier)
            tp = price + (atr_value * self.tp_atr_multiplier)
            direction_emoji = "📈"
        else:  # SELL
            sl = price + (atr_value * self.sl_atr_multiplier)
            tp = price - (atr_value * self.tp_atr_multiplier)
            direction_emoji = "📉"

        mt5_status = "✅ CONECTADO" if self.mt5_connected else "❌ DESCONECTADO"
        
        message = f"""
🚀 <b>SEÑAL DE TRADING - BOOM 1000</b> 🚀

{direction_emoji} <b>Dirección:</b> {direction}
💰 <b>Precio Entrada:</b> {price:.2f}
🎯 <b>Take Profit:</b> {tp:.2f}
🛑 <b>Stop Loss:</b> {sl:.2f}

📊 <b>Indicadores:</b>
   • RSI: {rsi_value:.1f}
   • ATR: {atr_value:.2f}

🤖 <b>MT5 Status:</b> {mt5_status}
⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}

#Trading #Señal #BOOM1000 #MT5
"""
        return message

    def display_signal(self, direction, price, atr_value, rsi_value):
        if direction == "BUY":
            sl = price - (atr_value * self.sl_atr_multiplier)
            tp = price + (atr_value * self.tp_atr_multiplier)
            color_code = "\033[92m"
        else:  # SELL
            sl = price + (atr_value * self.sl_atr_multiplier)
            tp = price - (atr_value * self.tp_atr_multiplier)
            color_code = "\033[91m"

        reset_code = "\033[0m"

        print("\n" + "="*60)
        print(f"🎯 {color_code}NUEVA SEÑAL DE TRADING - BOOM 1000{reset_code}")
        print("="*60)
        print(f"   📈 Dirección: {color_code}{direction}{reset_code}")
        print(f"   💰 Precio de Entrada: {price:.2f}")
        print(f"   🎯 Take Profit (TP): {tp:.2f} (Basado en ATR x{self.tp_atr_multiplier})")
        print(f"   🛑 Stop Loss (SL): {sl:.2f} (Basado en ATR x{self.sl_atr_multiplier})")
        print(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   📊 Info: RSI={rsi_value:.1f}, ATR={atr_value:.2f}")
        print("="*60)

        # Abrir operación en MT5 si está conectado
        if self.mt5_connected:
            print("🔄 Intentando abrir operación en MT5...")
            success = self.open_mt5_trade(direction, price, atr_value)
            if success:
                print("✅ Operación abierta exitosamente en MT5")
            else:
                print("❌ Error al abrir operación en MT5")

    def run_analyzer(self):
        print("\n" + "="*60)
        print("🤖 ANALIZADOR BOOM 1000 v2.0 - ESTRATEGIA DE VELAS")
        print("="*60)
        print("🧠 ESTRATEGIA:")
        print(f"   • Análisis en velas de {self.candle_interval_seconds} segundos.")
        print(f"   • Filtro de tendencia con EMA {self.ema_trend_period}.")
        print(f"   • Entrada por cruce de EMAs {self.ema_fast_period}/{self.ema_slow_period}.")
        print(f"   • TP/SL dinámico con ATR({self.atr_period}) x{self.tp_atr_multiplier}/{self.sl_atr_multiplier}.")

        if self.telegram_enabled:
            print("   📱 Notificaciones Telegram: ACTIVADAS")
        else:
            print("   📱 Notificaciones Telegram: DESACTIVADAS")

        if self.mt5_connected:
            print("   🤖 Trading MT5: ACTIVADO")
        else:
            print("   🤖 Trading MT5: DESACTIVADO")

        print("="*60)

        # Bucle principal con reconexión automática y auto-ping
        reconnect_interval = 15 * 60  # 15 minutos en segundos
        ping_interval = 10 * 60       # 10 minutos en segundos (antes de que Render duerma)
        
        last_ping_time = time.time()
        last_reconnect_time = time.time()

        while True:
            try:
                current_time = time.time()
                
                # Auto-ping cada 10 minutos para evitar que Render duerma el servicio
                if current_time - last_ping_time >= ping_interval:
                    print("🔄 Realizando auto-ping para mantener servicio activo...")
                    self.self_ping()
                    last_ping_time = current_time
                
                # Reconectar cada 15 minutos o si no está conectado
                if not self.connected or current_time - last_reconnect_time >= reconnect_interval:
                    if self.connected:
                        print("🔄 Reconexión programada (cada 15 minutos)...")
                        self.disconnect()
                        time.sleep(2)
                    
                    last_reconnect_time = current_time
                    
                    if self.connect():
                        print("✅ Reconexión exitosa")
                        # Bucle de análisis mientras esté conectado
                        while self.connected:
                            if self.new_candle_ready:
                                self.analyze_market()
                                self.new_candle_ready = False
                            time.sleep(1)
                    else:
                        print("❌ No se pudo conectar, reintentando en 30 segundos...")
                        time.sleep(30)
                else:
                    # Esperar hasta que sea tiempo de reconectar o hacer ping
                    next_action = min(
                        reconnect_interval - (current_time - last_reconnect_time),
                        ping_interval - (current_time - last_ping_time)
                    )
                    if next_action > 0:
                        sleep_time = min(60, next_action)  # Esperar máximo 1 minuto
                        print(f"⏰ Próxima acción en {sleep_time:.0f} segundos")
                        time.sleep(sleep_time)
                    
            except Exception as e:
                print(f"❌ Error crítico en run_analyzer: {e}")
                print("🔄 Reintentando en 30 segundos...")
                time.sleep(30)

# Crear instancia global del analizador
analyzer = None

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "BOOM 1000 Analyzer",
        "connected": analyzer.connected if analyzer else False,
        "mt5_connected": analyzer.mt5_connected if analyzer else False,
        "last_signal": analyzer.last_signal if analyzer else None,
        "total_candles": len(analyzer.candles) if analyzer else 0,
        "next_reconnect": analyzer.last_reconnect_time + (15 * 60) - time.time() if analyzer and hasattr(analyzer, 'last_reconnect_time') else 0
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "connected": analyzer.connected if analyzer else False,
        "mt5_connected": analyzer.mt5_connected if analyzer else False
    })

@app.route('/signals')
def signals():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    return jsonify({
        "last_signal": analyzer.last_signal,
        "history": analyzer.signals_history[-10:] if analyzer.signals_history else [],
        "total_signals": len(analyzer.signals_history)
    })

@app.route('/trades')
def trades():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    return jsonify({
        "trades": analyzer.trades_history[-10:] if analyzer.trades_history else [],
        "total_trades": len(analyzer.trades_history)
    })

@app.route('/mt5/status')
def mt5_status():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    return jsonify({
        "mt5_connected": analyzer.mt5_connected,
        "mt5_login": analyzer.mt5_login if analyzer.mt5_login else "Not configured",
        "mt5_symbol": analyzer.mt5_symbol
    })

@app.route('/mt5/positions')
def mt5_positions():
    if not analyzer or not analyzer.mt5_connected:
        return jsonify({"error": "MT5 not connected"})
    
    try:
        positions = mt5.positions_get()
        if positions is None:
            return jsonify({"positions": [], "count": 0})
        
        positions_list = []
        for pos in positions:
            positions_list.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "BUY" if pos.type == 0 else "SELL",
                "volume": pos.volume,
                "open_price": pos.price_open,
                "current_price": pos.price_current,
                "profit": pos.profit,
                "sl": pos.sl,
                "tp": pos.tp
            })
        
        return jsonify({
            "positions": positions_list,
            "count": len(positions_list)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/mt5/close_all')
def mt5_close_all():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    success = analyzer.close_all_mt5_trades()
    return jsonify({"success": success})

@app.route('/reconnect')
def manual_reconnect():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    analyzer.last_reconnect_time = 0  # Forzar reconexión inmediata
    return jsonify({"status": "reconnection_triggered", "message": "Se forzará la reconexión en el próximo ciclo"})

def cleanup():
    print("🛑 Cerrando conexiones...")
    if analyzer and analyzer.ws:
        analyzer.ws.close()
    if mt5.initialize():
        mt5.shutdown()
        print("✅ Conexión MT5 cerrada")

atexit.register(cleanup)

if __name__ == "__main__":
    # Configuración desde variables de entorno
    DEMO_TOKEN = os.environ.get("DERIV_TOKEN", "a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN", "7868591681:AAGYeuSUwozg3xTi1zmxPx9gWRP2xsXP0Uc")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "-1003028922957")
    
    # Configuración MT5 desde variables de entorno
    MT5_LOGIN = os.environ.get("31475757")
    MT5_PASSWORD = os.environ.get("Dyllan@2005")
    MT5_SERVER = os.environ.get("Deriv-Demo")
    MT5_LOT_SIZE = float(os.environ.get("MT5_LOT_SIZE", "0.5"))

    # Inicializar analizador
    analyzer = BOOM1000CandleAnalyzer(
        DEMO_TOKEN,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        mt5_login=MT5_LOGIN,
        mt5_password=MT5_PASSWORD,
        mt5_server=MT5_SERVER,
        mt5_lot_size=MT5_LOT_SIZE
    )
    
    # Iniciar servidor Flask
    print("🚀 Iniciando servidor Flask...")
    app.run(host='0.0.0.0', port=10000, debug=False)