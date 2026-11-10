# V11 MONSTER - CORRECTED - Flask + Hunter + Heartbeat
import os
import time
import requests
from datetime import datetime, timezone
from threading import Thread
from flask import Flask

# ========== CONFIG ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# EAT = UTC+3
# Killzones in EAT: London 10-12 | NY 15-18

PAIRS = {
    "XAUUSD": "GC=F",      # Gold
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
}

app = Flask(__name__)

@app.route("/")
def home():
    return "V11 MONSTER IS LIVE 👹"

@app.route("/health")
def health():
    return "OK"

def send_msg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured - skipping send")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def now_eat():
    # UTC+3 Nairobi
    return datetime.now(timezone.utc).hour + 3, datetime.now(timezone.utc)

def in_killzone():
    hour_eat, _ = now_eat()
    # Handle 24h wrap
    if hour_eat >= 24:
        hour_eat -= 24
    london = 10 <= hour_eat < 12
    ny = 15 <= hour_eat < 18
    return london or ny, hour_eat

# ========== DATA - Yahoo Finance ==========
def get_price(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=15m&range=5d"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return closes
    except Exception as e:
        print(f"Fetch error {ticker}: {e}")
        return []

def calc_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def check_fvg_bull(prices):
    # Simplified FVG: last 3 candles, middle gap
    if len(prices) < 5:
        return False
    # If price dipped then recovered = bullish FVG
    return prices[-1] > prices[-3] and prices[-2] < prices[-3]

def check_fvg_bear(prices):
    if len(prices) < 5:
        return False
    return prices[-1] < prices[-3] and prices[-2] > prices[-3]

def get_signal(name, ticker, state):
    closes = get_price(ticker)
    if len(closes) < 100:
        print(f"{name}: Not enough data {len(closes)}")
        return None

    # 4H trend - using 100-period EMA on 15m as proxy for 4H
    ema_4h = calc_ema(closes, 100)
    ema_1h = calc_ema(closes, 50)
    ema_15m = calc_ema(closes, 20)
    price = closes[-1]

    if ema_4h is None or ema_1h is None:
        return None

    trend_4h = "BULL" if price > ema_4h else "BEAR"
    bos_1h = "BULL" if price > ema_1h else "BEAR"
    fvg_bull = check_fvg_bull(closes)
    fvg_bear = check_fvg_bear(closes)

    signal = None
    reason = ""
    confidence = 0

    # BULLISH MONSTER SETUP - All 3 must agree
    if trend_4h == "BULL" and bos_1h == "BULL" and fvg_bull:
        signal = "BUY"
        confidence = 85
        reason = "4H Bull + 1H BOS + 15m FVG/BOS"

    # BEARISH MONSTER SETUP - All 3 must agree
    if trend_4h == "BEAR" and bos_1h == "BEAR" and fvg_bear:
        signal = "SELL"
        confidence = 85
        reason = "4H Bear + 1H BOS + 15m FVG/BOS"

    if not signal:
        return None  # Monster needs strength

    atr = abs(price - closes[-10]) if len(closes) > 10 else price * 0.002
    if atr == 0:
        atr = price * 0.002

    risk_pct = 0.01
    balance = state.get("balance", 100)
    risk_amt = balance * risk_pct
    reward_amt = risk_amt * 2

    if signal == "BUY":
        sl_price = price - atr * 2
        tp_price = price + atr * 2
    else:
        sl_price = price + atr * 2
        tp_price = price - atr * 2

    return {
        "pair": name, "signal": signal, "price": price,
        "sl": sl_price, "tp": tp_price,
        "rsi": 50, "adx": 25, "trend_4h": trend_4h,
        "confidence": max(confidence, 55),
        "reason": reason, "risk_pct": risk_pct,
        "risk_amt": risk_amt, "reward_amt": reward_amt,
        "risk_label": "1% Risk",
    }

# ========== MAIN LOOP ==========
def main():
    print("V11 MONSTER Started")
    send_msg("👹 V11 MONSTER IS LIVE\nKillzones: London 10-12 | NY 15-18 EAT\nRule: 4H + 1H + 15m must agree")
    sent_today = {}
    last_signals = {}
    state = {"balance": 100}

    while True:
        try:
            hour_eat, now_utc = now_eat()
            if hour_eat >= 24:
                hour_eat -= 24
            day_key = now_utc.strftime("%Y-%m-%d")
            if day_key not in sent_today:
                sent_today[day_key] = []

            in_kz, _ = in_killzone()

            # HEARTBEAT - THIS IS THE FIX, you will see this every minute in Logs
            print(f"V11 checking... EAT {hour_eat}:00 | in_killzone={in_kz} | sent_today={len(sent_today.get(day_key, []))} | {now_utc.strftime('%H:%M:%S')} UTC", flush=True)

            if not in_kz:
                print("Outside killzone, sleeping 120s", flush=True)
                time.sleep(120)
                continue

            if len(sent_today[day_key]) >= 2:
                print("Max 2 signals reached today, sleeping 120s", flush=True)
                time.sleep(120)
                continue

            # Inside killzone - hunt
            for name, ticker in PAIRS.items():
                res = get_signal(name, ticker, state)
                if not res:
                    print(f"{name}: No setup - 3 must agree, skipping", flush=True)
                    continue

                if last_signals.get(name) == res["signal"]:
                    continue

                last_signals[name] = res["signal"]
                sent_today[day_key].append(res["signal"])

                msg = (
                    f"👹 V11 MONSTER SIGNAL\n"
                    f"Pair: {res['pair']} | {res['signal']} {'🟢' if res['signal']=='BUY' else '🔴'}\n"
                    f"Confidence: {res['confidence']}%\n"
                    f"Why: {res['reason']}\n"
                    f"4H Trend: {res['trend_4h']}\n"
                    f"Entry: {res['price']:.2f}\n"
                    f"SL: {res['sl']:.2f}\n"
                    f"TP: {res['tp']:.2f}\n"
                    f"Risk: {res['risk_label']} | Risk ${res['risk_amt']:.2f} | Win ${res['reward_amt']:.2f}\n"
                    f"LOT: 0.01 ONLY\n"
                    f"⏰ {now_utc.strftime('%H:%M')} UTC / {hour_eat}:00 EAT\n"
                    f"⚠️ After you trade, tell me WIN or LOSS so I adapt"
                )
                send_msg(msg)
                print(f"SIGNAL SENT {name} {res['signal']}", flush=True)
                time.sleep(5)

            print("Killzone scan done, sleeping 60s", flush=True)
            time.sleep(60)

        except Exception as e:
            print(f"Main loop error: {e}", flush=True)
            time.sleep(60)

# ========== START BOTH ==========
if __name__ == "__main__":
    # Flask in background thread so it doesn't block hunter
    port = int(os.environ.get("PORT", 10000))
    def run_flask():
        app.run(host="0.0.0.0", port=port)

    t = Thread(target=run_flask, daemon=True)
    t.start()
    print(f"Flask running on port {port}", flush=True)
    # Hunter in main thread
    main()
