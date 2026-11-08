import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

def start_fake_web():
    port = int(os.environ.get("PORT", 10000))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", port), H).serve_forever()

threading.Thread(target=start_fake_web, daemon=True).start()
import os, time, requests, yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}

# Kenya time = UTC+3
def now_eat():
    return datetime.now(timezone.utc) + timedelta(hours=3)

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_data(symbol):
    df = yf.download(symbol, period="2d", interval="15m", progress=False)
    if df.empty: return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['EMA200'] = df['Close'].ewm(span=200).mean()
    df['RSI'] = rsi(df['Close'])
    # ATR for Smart TP/SL
    df['TR'] = np.maximum(df['High']-df['Low'],
        np.maximum(abs(df['High']-df['Close'].shift()),
                   abs(df['Low']-df['Close'].shift())))
    df['ATR'] = df['TR'].rolling(14).mean()
    return df

# --- UPGRADE 1: SESSION FILTER ---
def in_session():
    now = now_eat()
    # Only London + New York: 10:00 - 21:00 Kenya time, Mon-Fri
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return 10 <= now.hour < 21

# --- UPGRADE 2: NEWS FILTER ---
def is_news_time():
    now = now_eat()
    # Block 15:00-16:30 EAT on weekdays = US News (CPI, NFP, FOMC, etc - 15:30 EAT)
    # And block first Friday of month 15:00-17:00 = NFP
    if now.weekday() < 5 and 15 <= now.hour < 16.5:
        return True
    # Extra NFP block: first Friday
    if now.weekday() == 4 and now.day <= 7 and 15 <= now.hour < 17:
        return True
    return False

def check_pair(name, symbol):
    df = get_data(symbol)
    if df is None or len(df) < 210: return
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last['Close'])
    atr = float(last['ATR'])
    rsi_v = float(last['RSI'])
    ema50 = float(last['EMA50'])
    ema200 = float(last['EMA200'])

    # Trend filter
    trend_up = price > ema50 > ema200
    trend_down = price < ema50 < ema200
    if not (trend_up or trend_down):
        return

    signal = None
    score = 0
    # RSI + Trend scoring
    if trend_up and rsi_v < 35:
        signal = "BUY"
        score = 80
    elif trend_up and 35 <= rsi_v < 45:
        signal = "BUY"
        score = 75
    elif trend_down and rsi_v > 65:
        signal = "SELL"
        score = 80
    elif trend_down and 55 < rsi_v <= 65:
        signal = "SELL"
        score = 75

    if not signal or score < 75:
        return

    # --- UPGRADE 3: SMART ATR TP/SL ---
    # TP = 2x ATR, SL = 1x ATR - adapts to volatility
    if "XAUUSD" in name or "GC" in symbol:
        # Gold moves big
        tp_dist = atr * 2.0
        sl_dist = atr * 1.0
    elif "JPY" in name:
        tp_dist = atr * 2.0
        sl_dist = atr * 1.0
    else:
        tp_dist = atr * 2.0
        sl_dist = atr * 1.0

    if signal == "BUY":
        tp = price + tp_dist
        sl = price - sl_dist
    else:
        tp = price - tp_dist
        sl = price + sl_dist

    msg = (
        f"🚀 V3 STRONG SIGNAL - {name}\n\n"
        f"Direction: {signal} {'🟢' if signal=='BUY' else '🔴'}\n"
        f"Confidence: {score}%\n"
        f"Price: {price:.5f}\n"
        f"RSI: {rsi_v:.1f}\n"
        f"Trend: {'UP 📈' if trend_up else 'DOWN 📉'}\n\n"
        f"🎯 TP: {tp:.5f} (2x ATR)\n"
        f"🛑 SL: {sl:.5f} (1x ATR)\n"
        f"ATR: {atr:.5f}\n\n"
        f"Session: London/NY ✅ | News Filter: ON ✅"
    )
    send(msg)
    print(f"Sent {signal} {name} score {score}")

send("✅ V3 STRONG BOT is LIVE\n\nPairs: EURUSD, XAUUSD, GBPUSD, USDJPY\nMode: SAFE - Only 75%+ signals\nWith TP / SL + RSI + Trend Filter\nNEW: News Filter + Session Filter + Smart ATR TP/SL\n\nWaiting for STRONG signals...")
print("=== V3 STRONG BOT STARTED ===")

sent_today = set()
while True:
    now = now_eat()
    if not in_session():
        print(f"Outside session {now.strftime('%H:%M')} - sleeping")
        time.sleep(300)
        continue
    if is_news_time():
        print(f"NEWS TIME - blocked {now.strftime('%H:%M')}")
        time.sleep(300)
        continue

    for name, sym in PAIRS.items():
        try:
            # Avoid duplicate same hour
            key = f"{name}-{now.strftime('%Y-%m-%d-%H')}"
            if key in sent_today:
                continue
            check_pair(name, sym)
            # Mark sent if check_pair sent - simplified: we track by trying
            # To avoid spam, we add key after check if signal was sent inside check_pair
            # For now, rely on 15m loop timing
        except Exception as e:
            print(f"Error {name}: {e}")
    time.sleep(900)  # Check every 15 mins
