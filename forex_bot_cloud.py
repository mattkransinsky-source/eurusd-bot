import os, time, requests, math
from datetime import datetime

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

PAIRS = {
    "EURUSD": {"symbol": "EURUSD", "decimals": 5},
    "XAUUSD": {"symbol": "GOLD", "decimals": 2},
    "GBPUSD": {"symbol": "GBPUSD", "decimals": 5},
    "USDJPY": {"symbol": "USDJPY", "decimals": 3},
}

# Store history for each pair
history = {k: [] for k in PAIRS}
last_signal = {k: "" for k in PAIRS}

def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        print("Telegram not set - skipping")
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print("Telegram sent!" if r.status_code == 200 else f"Telegram failed {r.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_prices():
    prices = {}
    try:
        # Forex from open ER API
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10).json()
        rates = r.get("rates", {})
        if "EUR" in rates: prices["EURUSD"] = 1 / rates["EUR"]
        if "GBP" in rates: prices["GBPUSD"] = 1 / rates["GBP"]
        if "JPY" in rates: prices["USDJPY"] = rates["JPY"]
    except Exception as e:
        print(f"Forex price error: {e}")
    try:
        # Gold
        r = requests.get("https://data-asg.goldprice.org/db/XAUUSD.json", timeout=10).json()
        prices["XAUUSD"] = float(r["items"][0]["curr"])
    except Exception as e:
        print(f"Gold price error: {e}")
    return prices

def ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    k = 2 / (period + 1)
    e = sum(data[:period]) / period
    for p in data[period:]:
        e = p * k + e * (1 - k)
    return e

def rsi(data, period=14):
    if len(data) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, period+1):
        diff = data[-i] - data[-i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains)/period
    avg_loss = sum(losses)/period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze(pair, price):
    h = history[pair]
    h.append(price)
    if len(h) > 100: h.pop(0)
    if len(h) < 20:
        print(f"{pair}: {price} - Collecting data... {len(h)}/20")
        return

    fast = ema(h, 9)
    slow = ema(h, 21)
    trend = ema(h, 50)
    r = rsi(h)

    # ATR for TP/SL - volatility
    atr = abs(h[-1] - h[-10]) / 10 if len(h) >= 10 else price * 0.001
    if atr == 0: atr = price * 0.001

    signal = ""
    confidence = 0
    reason = ""

    # STRONG BUY: Fast > Slow, Price > Trend, RSI not overbought
    if fast > slow and price > trend and r < 70:
        signal = "BUY"
        confidence = 65
        if r < 55: confidence += 10
        if fast > slow * 1.0005: confidence += 10
        reason = f"EMA Bullish + Trend Up + RSI {r:.0f}"
    # STRONG SELL
    elif fast < slow and price < trend and r > 30:
        signal = "SELL"
        confidence = 65
        if r > 45: confidence += 10
        if fast < slow * 0.9995: confidence += 10
        reason = f"EMA Bearish + Trend Down + RSI {r:.0f}"

    print(f"{pair}: {price:.5f} | Fast:{fast:.5f} Slow:{slow:.5f} RSI:{r:.0f} -> {signal or 'HOLD'} Conf:{confidence}%")

    # SAFE MODE: Only send if confidence 75%+ and signal changed
    if signal and confidence >= 75 and last_signal[pair] != signal:
        last_signal[pair] = signal

        if signal == "BUY":
            tp = price + (atr * 3)
            sl = price - (atr * 1.5)
            emoji = "🟢"
        else:
            tp = price - (atr * 3)
            sl = price + (atr * 1.5)
            emoji = "🔴"

        msg = f"""{emoji} *STRONG {signal} - {pair}*

💰 Price: `{price:.5f}`
📊 Confidence: *{confidence}%*
📈 RSI: {r:.0f} | Trend: {'UP' if price > trend else 'DOWN'}
🎯 Take Profit: `{tp:.5f}`
🛑 Stop Loss: `{sl:.5f}`
💡 {reason}

⚠️ SAFE MODE - Only 75%+ signals
⏰ {datetime.now().strftime('%H:%M %d %b')}"""
        send_telegram(msg)
    elif not signal:
        # Reset when back to HOLD so next signal can fire
        if abs(fast - slow) < price * 0.0002:
            last_signal[pair] = ""

print("=== V2 STRONG BOT STARTED ===")
print("Pairs: EURUSD, XAUUSD, GBPUSD, USDJPY | SAFE MODE 75%+")
send_telegram("✅ *V2 STRONG BOT is LIVE*\n\nPairs: EURUSD, XAUUSD, GBPUSD, USDJPY\nMode: SAFE - Only 75%+ signals\nWith TP / SL + RSI + Trend Filter\n\nWaiting for STRONG signals...")

while True:
    try:
        prices = get_prices()
        for pair, price in prices.items():
            analyze(pair, price)
        print("--- Waiting 60s ---")
        time.sleep(60)
    except Exception as e:
        print(f"Loop error: {e}")
        time.sleep(30)
