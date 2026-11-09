import os
import asyncio
import requests
import threading
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# Accept BOTH token names so it never says False again
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1}

last_signals = {}
signals_today = 0
last_signal_date = None

def now_eat():
    return datetime.now() + timedelta(hours=3)

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"V8 SIGNALS LIVE")
        def log_message(self, *args):
            pass
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        print(f"Web server failed: {e}")
threading.Thread(target=keep_alive, daemon=True).start()

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"send status: {r.status_code} - {msg[:60]}")
        return r.status_code
    except Exception as e:
        print(f"Telegram failed: {e}")
        return 0

def get_closes(symbol):
    try:
        for base in ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]:
            url = f"{base}/v8/finance/chart/{symbol}?interval=15m&range=5d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                closes = res["indicators"]["quote"][0]["close"]
                closes = [c for c in closes if c is not None]
                if len(closes) >= 100:
                    return closes
        return None
    except Exception as e:
        print(f"Data error {symbol}: {e}")
        return None

def calc_rsi(closes, period=14):
    if len(closes) < period + 2:
        return 50
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i-1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.00001
    rs = avg_gain / (avg_loss + 0.0000001)
    return 100 - (100 / (1 + rs))

def calc_ema(closes, span):
    if len(closes) < span:
        return closes[-1]
    k = 2 / (span + 1)
    ema = closes[-span]
    for price in closes[-span:]:
        ema = price * k + ema * (1 - k)
    return ema

def check_pair(name, symbol):
    closes = get_closes(symbol)
    if not closes:
        return None
    price = closes[-1]
    ema50 = calc_ema(closes, 50)
    ema200 = calc_ema(closes, 200)
    rsi = calc_rsi(closes)

    trend_up = price > ema50 > ema200
    trend_down = price < ema50 < ema200

    signal = None
    score = 0
    if trend_up and 45 < rsi < 68:
        signal = "BUY"
        score = 80
        if 55 <= rsi <= 65:
            score = 85
    elif trend_down and 32 < rsi < 55:
        signal = "SELL"
        score = 80
        if 35 <= rsi <= 45:
            score = 85

    if not signal:
        return None
    return {"pair": name, "signal": signal, "price": price, "rsi": rsi, "ema50": ema50, "ema200": ema200, "score": score}

async def heartbeat():
    while True:
        await asyncio.sleep(7200)
        send_telegram(f"💓 V8 Heartbeat - Still Alive\nTime: {now_eat().strftime('%H:%M')} EAT\nPairs: EURUSD, XAUUSD, GBPUSD, USDJPY\nSignals Today: {signals_today}\nLast: {last_signals}")

async def main():
    global signals_today, last_signal_date
    print(f"TELEGRAM_TOKEN set: {bool(TELEGRAM_TOKEN)}")
    print(f"TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
    send_telegram("✅ V8 is LIVE - IMPROVED\n\nPairs: EURUSD, XAUUSD, GBPUSD, USDJPY\nFilter: RSI + EMA50 + EMA200 - All must agree\nInfo: TP1 / TP2 / SL in every signal\nTime: Kenya Time (EAT) FIXED\nSignals Only - NO Auto Trading")
    asyncio.create_task(heartbeat())

    while True:
        today = date.today()
        if last_signal_date != today:
            signals_today = 0
            last_signal_date = today
            last_signals.clear()

        for name, symbol in PAIRS.items():
            try:
                result = check_pair(name, symbol)
                if result:
                    if last_signals.get(name) == result["signal"]:
                        print(f"{name} - Same signal {result['signal']} - skip")
                        continue
                    last_signals[name] = result["signal"]
                    signals_today += 1
                    price = result["price"]
                    pip = PIP[name]
                    if result["signal"] == "BUY":
                        sl = price - (20 * pip)
                        tp1 = price + (20 * pip)
                        tp2 = price + (40 * pip)
                        emoji = "🟢"
                    else:
                        sl = price + (20 * pip)
                        tp1 = price - (20 * pip)
                        tp2 = price - (40 * pip)
                        emoji = "🔴"

                    msg = (
                        f"{emoji} V8 SIGNAL - {name} {result['signal']}\n\n"
                        f"Price: {price:.5f}\n"
                        f"Score: {result['score']}% | RSI: {result['rsi']:.1f}\n"
                        f"EMA50: {result['ema50']:.5f} | EMA200: {result['ema200']:.5f}\n"
                        f"Trend: {'UP 📈' if result['signal']=='BUY' else 'DOWN 📉'}\n\n"
                        f"🎯 TP1: {tp1:.5f}\n"
                        f"🎯 TP2: {tp2:.5f}\n"
                        f"🛑 SL: {sl:.5f}\n\n"
                        f"⏰ {now_eat().strftime('%H:%M')} EAT | Valid 30 mins\n"
                        f"⚠️ Signals only - YOU decide"
                    )
                    send_telegram(msg)
                    print(f"SIGNAL SENT: {name} {result['signal']} at {price}")
                else:
                    print(f"{name} - No setup - waiting")
            except Exception as e:
                print(f"Error {name}: {e}")
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
