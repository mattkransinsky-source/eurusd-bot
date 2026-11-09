import os
import asyncio
import requests
import threading
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.1}
# IMPROVEMENT 1: Correlation groups - EURUSD, GBPUSD, XAUUSD move together
CORR_GROUP = {"EURUSD": "USD", "GBPUSD": "USD", "XAUUSD": "USD", "USDJPY": "JPY"}

last_signals = {}
signals_today = 0
last_signal_date = None
sent_this_hour = {}  # hour_key -> list of (group, direction)

def now_eat():
    return datetime.now() + timedelta(hours=3)

# IMPROVEMENT 2: Session Filter - London + NY only
def in_session():
    now = now_eat()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return 10 <= now.hour < 21  # 10:00 - 21:00 EAT, Mon-Fri

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"V9 SIGNALS LIVE")
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

def get_data(symbol):
    try:
        for base in ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]:
            url = f"{base}/v8/finance/chart/{symbol}?interval=15m&range=5d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                closes = [c for c in q["close"] if c is not None]
                highs = [c for c in q["high"] if c is not None]
                lows = [c for c in q["low"] if c is not None]
                if len(closes) >= 100:
                    return closes, highs, lows
        return None, None, None
    except Exception as e:
        print(f"Data error {symbol}: {e}")
        return None, None, None

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

def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return abs(closes[-1] * 0.001)
    trs = []
    for i in range(1, period + 1):
        tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i-1]), abs(lows[-i] - closes[-i-1]))
        trs.append(tr)
    return sum(trs) / len(trs)

def check_pair(name, symbol):
    closes, highs, lows = get_data(symbol)
    if not closes:
        return None
    price = closes[-1]
    ema50 = calc_ema(closes, 50)
    ema200 = calc_ema(closes, 200)
    rsi = calc_rsi(closes)
    atr = calc_atr(highs, lows, closes)

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
    return {"pair": name, "signal": signal, "price": price, "rsi": rsi, "ema50": ema50, "ema200": ema200, "score": score, "atr": atr}

async def heartbeat():
    while True:
        await asyncio.sleep(7200)
        send_telegram(f"💓 V9 Heartbeat - Still Alive\nTime: {now_eat().strftime('%H:%M')} EAT\nSession: 10:00-21:00 EAT Only\nSignals Today: {signals_today}\nLast: {last_signals}")

async def main():
    global signals_today, last_signal_date
    print(f"TELEGRAM_TOKEN set: {bool(TELEGRAM_TOKEN)}")
    print(f"TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
    send_telegram("✅ V9 is LIVE - SMARTER\n\nPairs: EURUSD, XAUUSD, GBPUSD, USDJPY\nNEW 1: Correlation Block - No double EURUSD+GBPUSD\nNEW 2: Session Filter - 10:00-21:00 EAT Only\nNEW 3: Smart ATR TP/SL - Adapts to volatility\nFilter: RSI + EMA50 + EMA200\nSignals Only - NO Auto Trading")
    asyncio.create_task(heartbeat())

    while True:
        today = date.today()
        if last_signal_date != today:
            signals_today = 0
            last_signal_date = today
            last_signals.clear()
            sent_this_hour.clear()

        # Session filter - don't check outside session, just wait
        if not in_session():
            print(f"Outside session {now_eat().strftime('%H:%M')} EAT - sleeping")
            await asyncio.sleep(300)
            continue

        hour_key = now_eat().strftime("%Y-%m-%d-%H")
        if hour_key not in sent_this_hour:
            sent_this_hour[hour_key] = []

        for name, symbol in PAIRS.items():
            try:
                result = check_pair(name, symbol)
                if result:
                    if last_signals.get(name) == result["signal"]:
                        print(f"{name} - Same signal {result['signal']} - skip")
                        continue

                    # IMPROVEMENT 1: Correlation Block
                    group = CORR_GROUP[name]
                    blocked = False
                    for (g, d) in sent_this_hour[hour_key]:
                        if g == group and d == result["signal"]:
                            print(f"{name} - BLOCKED by correlation: {g} already sent {d} this hour")
                            blocked = True
                            break
                    if blocked:
                        continue

                    last_signals[name] = result["signal"]
                    sent_this_hour[hour_key].append((group, result["signal"]))
                    signals_today += 1
                    price = result["price"]
                    atr = result["atr"]

                    # IMPROVEMENT 3: Smart ATR TP/SL - TP 2x ATR, SL 1x ATR
                    if result["signal"] == "BUY":
                        sl = price - (atr * 1.0)
                        tp1 = price + (atr * 1.0)
                        tp2 = price + (atr * 2.0)
                        emoji = "🟢"
                    else:
                        sl = price + (atr * 1.0)
                        tp1 = price - (atr * 1.0)
                        tp2 = price - (atr * 2.0)
                        emoji = "🔴"

                    # For display, show ATR in pips
                    atr_pips = atr / PIP[name]

                    msg = (
                        f"{emoji} V9 SIGNAL - {name} {result['signal']}\n\n"
                        f"Price: {price:.5f}\n"
                        f"Score: {result['score']}% | RSI: {result['rsi']:.1f}\n"
                        f"EMA50: {result['ema50']:.5f} | EMA200: {result['ema200']:.5f}\n"
                        f"Trend: {'UP 📈' if result['signal']=='BUY' else 'DOWN 📉'}\n"
                        f"ATR: {atr_pips:.1f} pips\n\n"
                        f"🎯 TP1: {tp1:.5f} (1x ATR)\n"
                        f"🎯 TP2: {tp2:.5f} (2x ATR)\n"
                        f"🛑 SL: {sl:.5f} (1x ATR)\n\n"
                        f"⏰ {now_eat().strftime('%H:%M')} EAT | Valid 30 mins\n"
                        f"⚠️ Signals only - YOU decide"
                    )
                    send_telegram(msg)
                    print(f"SIGNAL SENT: {name} {result['signal']} at {price} ATR {atr_pips:.1f} pips")
                else:
                    print(f"{name} - No setup - waiting")
            except Exception as e:
                print(f"Error {name}: {e}")
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
