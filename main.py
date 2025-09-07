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

app = Flask(__name__)

class BOOM1000CandleAnalyzer:
    def __init__(self, token, app_id="88258", telegram_token=None, telegram_chat_id=None):
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
        self.sl_atr_multiplier = 1.5
        self.tp_atr_multiplier = 2.0

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
        
        # --- Alertas de Entrada Inminente ---
        self.potential_signal = None
        self.last_alert_time = 0
        self.alert_cooldown = 30  # segundos entre alertas de entrada inminente

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

    def calculate_support_resistance(self, closes, lookback=20):
        """Calcula niveles de soporte y resistencia basados en máximos y mínimos recientes"""
        if len(closes) < lookback:
            return None, None
        
        # Encontrar máximos y mínimos locales
        recent_data = closes[-lookback:]
        resistance = np.max(recent_data)
        support = np.min(recent_data)
        
        return support, resistance

    def calculate_pivot_points(self, high, low, close):
        """Calcula puntos pivote clásicos"""
        pivot = (high + low + close) / 3
        r1 = (2 * pivot) - low
        s1 = (2 * pivot) - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        
        return {
            'pivot': pivot,
            'r1': r1, 'r2': r2,
            's1': s1, 's2': s2
        }

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

    def check_potential_signal(self, ema_fast, ema_slow, ema_trend, rsi):
        """Verifica si hay una señal potencial inminente"""
        current_time = time.time()
        
        # Verificar si ya pasó el tiempo de enfriamiento para alertas
        if current_time - self.last_alert_time < self.alert_cooldown:
            return None
            
        # Verificar si las EMAs están muy cerca de cruzarse
        fast_current = ema_fast[-1]
        slow_current = ema_slow[-1]
        fast_prev = ema_fast[-2] if len(ema_fast) > 1 else fast_current
        slow_prev = ema_slow[-2] if len(ema_slow) > 1 else slow_current
        
        # Calcular la distancia porcentual entre las EMAs
        distance_percent = abs(fast_current - slow_current) / slow_current * 100
        
        # Si las EMAs están muy cerca (menos del 0.5% de diferencia)
        if distance_percent < 0.5:
            # Determinar dirección potencial basada en la tendencia y posición relativa
            is_uptrend = ema_fast[-1] > ema_trend[-1] and ema_slow[-1] > ema_trend[-1]
            is_downtrend = ema_fast[-1] < ema_trend[-1] and ema_slow[-1] < ema_trend[-1]
            
            # Verificar condiciones RSI para ambas direcciones
            rsi_ok_buy = 40 < rsi[-1] < 70
            rsi_ok_sell = 30 < rsi[-1] < 60
            
            if is_uptrend and fast_current > slow_current and rsi_ok_buy:
                return "BUY"
            elif is_downtrend and fast_current < slow_current and rsi_ok_sell:
                return "SELL"
                
        return None

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
            
            # Calcular niveles de soporte y resistencia
            support, resistance = self.calculate_support_resistance(closes)
            
            # Calcular puntos pivote para la última vela
            last_high = highs[-1]
            last_low = lows[-1]
            last_close = closes[-1]
            pivot_points = self.calculate_pivot_points(last_high, last_low, last_close)
            
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

        # Verificar señal potencial inminente
        potential_signal = self.check_potential_signal(ema_fast, ema_slow, ema_trend, rsi)
        if potential_signal and potential_signal != self.potential_signal:
            self.potential_signal = potential_signal
            self.last_alert_time = current_time
            self.alert_potential_signal(potential_signal, last_close, rsi[-1])

        if current_time - self.last_signal_time < self.signal_cooldown:
            return

        # Señal de COMPRA (BUY)
        if is_uptrend and len(ema_fast) > 1 and ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            if rsi[-1] > 40 and rsi[-1] < 70:
                signal = "BUY"
                self.potential_signal = None  # Resetear señal potencial

        # Señal de VENTA (SELL)
        if is_downtrend and len(ema_fast) > 1 and ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            if rsi[-1] < 60 and rsi[-1] > 30:
                signal = "SELL"
                self.potential_signal = None  # Resetear señal potencial

        if signal:
            # Análisis dinámico de TP/SL basado en múltiples factores
            if signal == "BUY":
                # Para compras, buscar resistencias cercanas como posibles TP
                tp_candidates = []
                
                # 1. Resistencia más cercana
                if resistance and resistance > last_close:
                    tp_candidates.append(resistance)
                
                # 2. Niveles de pivote (R1, R2)
                if pivot_points['r1'] > last_close:
                    tp_candidates.append(pivot_points['r1'])
                if pivot_points['r2'] > last_close:
                    tp_candidates.append(pivot_points['r2'])
                
                # 3. TP basado en ATR si no hay niveles claros
                atr_tp = last_close + (last_atr * self.tp_atr_multiplier)
                tp_candidates.append(atr_tp)
                
                # Seleccionar el TP más conservador (más cercano)
                dynamic_tp = min(tp_candidates) if tp_candidates else atr_tp
                
                # Análisis de SL
                sl_candidates = []
                
                # 1. Soporte más cercano
                if support and support < last_close:
                    sl_candidates.append(support)
                
                # 2. Niveles de pivote (S1, S2)
                if pivot_points['s1'] < last_close:
                    sl_candidates.append(pivot_points['s1'])
                if pivot_points['s2'] < last_close:
                    sl_candidates.append(pivot_points['s2'])
                
                # 3. SL basado en ATR si no hay niveles claros
                atr_sl = last_close - (last_atr * self.sl_atr_multiplier)
                sl_candidates.append(atr_sl)
                
                # Seleccionar el SL más conservador (más cercano)
                dynamic_sl = max(sl_candidates) if sl_candidates else atr_sl
                
            else:  # SELL
                # Para ventas, buscar soportes cercanos como posibles TP
                tp_candidates = []
                
                # 1. Soporte más cercano
                if support and support < last_close:
                    tp_candidates.append(support)
                
                # 2. Niveles de pivote (S1, S2)
                if pivot_points['s1'] < last_close:
                    tp_candidates.append(pivot_points['s1'])
                if pivot_points['s2'] < last_close:
                    tp_candidates.append(pivot_points['s2'])
                
                # 3. TP basado en ATR si no hay niveles claros
                atr_tp = last_close - (last_atr * self.tp_atr_multiplier)
                tp_candidates.append(atr_tp)
                
                # Seleccionar el TP más conservador (más cercano)
                dynamic_tp = max(tp_candidates) if tp_candidates else atr_tp
                
                # Análisis de SL
                sl_candidates = []
                
                # 1. Resistencia más cercana
                if resistance and resistance > last_close:
                    sl_candidates.append(resistance)
                
                # 2. Niveles de pivote (R1, R2)
                if pivot_points['r1'] > last_close:
                    sl_candidates.append(pivot_points['r1'])
                if pivot_points['r2'] > last_close:
                    sl_candidates.append(pivot_points['r2'])
                
                # 3. SL basado en ATR si no hay niveles claros
                atr_sl = last_close + (last_atr * self.sl_atr_multiplier)
                sl_candidates.append(atr_sl)
                
                # Seleccionar el SL más conservador (más cercano)
                dynamic_sl = min(sl_candidates) if sl_candidates else atr_sl

            self.last_signal_time = current_time
            self.last_signal = {
                'direction': signal,
                'price': last_close,
                'tp': dynamic_tp,
                'sl': dynamic_sl,
                'atr': last_atr,
                'rsi': rsi[-1],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'analysis_type': 'dynamic'
            }
            self.signals_history.append(self.last_signal)
            
            self.display_signal(signal, last_close, dynamic_tp, dynamic_sl, rsi[-1])

            # Enviar señal a Telegram
            if self.telegram_enabled:
                telegram_msg = self.format_telegram_message(signal, last_close, dynamic_tp, dynamic_sl, rsi[-1])
                self.send_telegram_message(telegram_msg)

    def alert_potential_signal(self, direction, price, rsi_value):
        """Envía alerta de señal potencial inminente"""
        current_time = time.time()
        
        if direction == "BUY":
            color_code = "\033[92m"
            emoji = "📈"
        else:  # SELL
            color_code = "\033[91m"
            emoji = "📉"
            
        reset_code = "\033[0m"
        
        print("\n" + "="*60)
        print(f"🔔 {color_code}ALERTA: POSIBLE SEÑAL {direction} INMINENTE{reset_code} {emoji}")
        print("="*60)
        print(f"   📊 Condiciones favorables para {direction}")
        print(f"   💰 Precio actual: {price:.2f}")
        print(f"   📈 RSI: {rsi_value:.1f}")
        print(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        print("   ⚠️  Las EMAs están muy cerca de un cruce")
        print("="*60)
        
        # Enviar alerta a Telegram si está configurado
        if self.telegram_enabled:
            telegram_msg = self.format_potential_signal_message(direction, price, rsi_value)
            self.send_telegram_message(telegram_msg)

    def format_potential_signal_message(self, direction, price, rsi_value):
        """Formatea mensaje de Telegram para señal potencial"""
        if direction == "BUY":
            emoji = "📈"
        else:
            emoji = "📉"
            
        return f"""
🔔 <b>ALERTA: POSIBLE SEÑAL {direction} INMINENTE</b> {emoji}

📊 <b>Condiciones:</b> Posible señal de {direction} en formación
💰 <b>Precio actual:</b> {price:.2f}
📈 <b>RSI:</b> {rsi_value:.1f}
⚠️  <b>Nota:</b> Las EMAs están muy cerca de un cruce

⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}

#Alerta #BOOM1000 #Trading
"""

    def format_telegram_message(self, direction, price, tp, sl, rsi_value):
        if direction == "BUY":
            direction_emoji = "📈"
        else:  # SELL
            direction_emoji = "📉"

        message = f"""
🚀 <b>SEÑAL DE TRADING - BOOM 1000</b> 🚀

{direction_emoji} <b>Dirección:</b> {direction}
💰 <b>Precio Entrada:</b> {price:.2f}
🎯 <b>Take Profit:</b> {tp:.2f}
🛑 <b>Stop Loss:</b> {sl:.2f}

📊 <b>Indicadores:</b>
   • RSI: {rsi_value:.1f}

🔍 <b>Análisis:</b> TP/SL dinámicos basados en soportes/resistencias y ATR

⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}

#Trading #Señal #BOOM1000
"""
        return message

    def display_signal(self, direction, price, tp, sl, rsi_value):
        if direction == "BUY":
            color_code = "\033[92m"
        else:  # SELL
            color_code = "\033[91m"

        reset_code = "\033[0m"

        print("\n" + "="*60)
        print(f"🎯 {color_code}NUEVA SEÑAL DE TRADING - BOOM 1000{reset_code}")
        print("="*60)
        print(f"   📈 Dirección: {color_code}{direction}{reset_code}")
        print(f"   💰 Precio de Entrada: {price:.2f}")
        print(f"   🎯 Take Profit (TP): {tp:.2f} (Dinámico - Basado en análisis)")
        print(f"   🛑 Stop Loss (SL): {sl:.2f} (Dinámico - Basado en análisis)")
        print(f"   ⏰ Hora: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   📊 Info: RSI={rsi_value:.1f}")
        print("="*60)

    def run_analyzer(self):
        print("\n" + "="*60)
        print("🤖 ANALIZADOR BOOM 1000 v2.0 - ESTRATEGIA DE VELAS")
        print("="*60)
        print("🧠 ESTRATEGIA:")
        print(f"   • Análisis en velas de {self.candle_interval_seconds} segundos.")
        print(f"   • Filtro de tendencia con EMA {self.ema_trend_period}.")
        print(f"   • Entrada por cruce de EMAs {self.ema_fast_period}/{self.ema_slow_period}.")
        print("   • TP/SL DINÁMICOS basados en soportes/resistencias y ATR")
        print("   • 🔔 ALERTAS de señales inminentes")

        if self.telegram_enabled:
            print("   📱 Notificaciones Telegram: ACTIVADAS")
        else:
            print("   📱 Notificaciones Telegram: DESACTIVADAS")

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
        "last_signal": analyzer.last_signal if analyzer else None,
        "potential_signal": analyzer.potential_signal if analyzer else None,
        "total_candles": len(analyzer.candles) if analyzer else 0,
        "next_reconnect": analyzer.last_reconnect_time + (15 * 60) - time.time() if analyzer and hasattr(analyzer, 'last_reconnect_time') else 0
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "connected": analyzer.connected if analyzer else False
    })

@app.route('/signals')
def signals():
    if not analyzer:
        return jsonify({"error": "Analyzer not initialized"})
    
    return jsonify({
        "last_signal": analyzer.last_signal,
        "potential_signal": analyzer.potential_signal,
        "history": analyzer.signals_history[-10:] if analyzer.signals_history else [],
        "total_signals": len(analyzer.signals_history)
    })

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

atexit.register(cleanup)

if __name__ == "__main__":
    # Configuración
    DEMO_TOKEN = "a1-m63zGttjKYP6vUq8SIJdmySH8d3Jc"
    TELEGRAM_BOT_TOKEN = "7868591681:AAGYeuSUwozg3xTi1zmxPx9gWRP2xsXP0Uc"
    TELEGRAM_CHAT_ID = "-1003028922957"

    # Inicializar analizador
    analyzer = BOOM1000CandleAnalyzer(
        DEMO_TOKEN,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    
    # Iniciar servidor Flask
    print("🚀 Iniciando servidor Flask...")
    app.run(host='0.0.0.0', port=10000, debug=False)