import threading, os
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home(): return "V10 Aggressive Running"
def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=run_web, daemon=True).start()
import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
}
CORR_GROUP = {"EURUSD": "USD", "GBPUSD": "USD", "XAUUSD": "USD", "USDJPY": "JPY"}

def now_eat():
    return datetime.now() + timedelta(hours=3)

def in_session():
    now = now_eat()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 22  # AGGRESSIVE: 09:00-22:00

def in_news_block():
    now = now_eat()
    if now.weekday() in [2, 4]:
        if 15 <= now.hour < 17:
            return True
    if now.weekday() == 1 and 15 <= now.hour < 16:
        return True
    return False

def send_msg(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"TG error: {e}")
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        except: pass

def calc_ema(df, span):
    return df['Close'].ewm(span=span, adjust=False).mean().iloc[-1]

def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 0.0000001)
    return 100 - (100 / (1 + rs)).iloc[-1]

def calc_atr(df, period=14):
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def calc_adx(df, period=14):
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    up = high.diff(); down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    plus_di = 100 * (plus_dm.rolling(period).mean() / (atr + 0.0000001))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr + 0.0000001))
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 0.0000001))
    return dx.rolling(period).mean().iloc[-1]

def check_bos_fvg(df):
    closes = df['Close']; highs = df['High']; lows = df['Low']
    swing_high = highs.iloc[-21:-1].max()
    swing_low = lows.iloc[-21:-1].min()
    last_close = closes.iloc[-1]; prev_close = closes.iloc[-2]
    bos_up = prev_close < swing_high and last_close > swing_high
    bos_down = prev_close > swing_low and last_close < swing_low
    fvg_bull = False; fvg_bear = False
    for i in range(-6, -2):
        if lows.iloc[i] > highs.iloc[i+2]: fvg_bull = True
        if highs.iloc[i] < lows.iloc[i+2]: fvg_bear = True
    return bos_up, bos_down, fvg_bull, fvg_bear

def get_signal(name, ticker):
    try:
        df15 = yf.download(ticker, period="5d", interval="15m", progress=False, auto_adjust=False)
        if df15 is None or len(df15) < 210: return None
        if isinstance(df15.columns, pd.MultiIndex): df15.columns = df15.columns.get_level_values(0)
        df15 = df15.dropna()
        df1h = yf.download(ticker, period="10d", interval="1h", progress=False, auto_adjust=False)
        if df1h is not None and len(df1h) > 210:
            if isinstance(df1h.columns, pd.MultiIndex): df1h.columns = df1h.columns.get_level_values(0)
            df1h = df1h.dropna()
            ema200_1h = calc_ema(df1h, 200); ema50_1h = calc_ema(df1h, 50)
        else: ema200_1h = ema50_1h = None

        price = float(df15['Close'].iloc[-1])
        ema50 = calc_ema(df15, 50); ema200 = calc_ema(df15, 200)
        rsi = calc_rsi(df15['Close']); atr = calc_atr(df15); adx = calc_adx(df15)
        bos_up, bos_down, fvg_bull, fvg_bear = check_bos_fvg(df15)

        signal = None
        if price > ema50 > ema200 and 42 < rsi < 70: signal = "BUY"
        elif price < ema50 < ema200 and 30 < rsi < 58: signal = "SELL"
        if not signal: return None

        # AGGRESSIVE FILTERS - looser
        if adx < 15:  # was 20, now 15 = more trades
            print(f"{name} blocked ADX {adx:.1f}")
            return None
        if ema50_1h is not None:
            if signal == "BUY" and not (price > ema50_1h):
                return None
            if signal == "SELL" and not (price < ema50_1h):
                return None
        # BOS OR FVG is enough in aggressive (was BOS required)
        if signal == "BUY" and not (bos_up or fvg_bull): return None
        if signal == "SELL" and not (bos_down or fvg_bear): return None

        has_fvg = (fvg_bull if signal == "BUY" else fvg_bear)
        quality = "🔥 AGGRESSIVE A+ (BOS + FVG)" if has_fvg else "⚡ AGGRESSIVE A (BOS/FVG)"

        # AGGRESSIVE TP = 1.5R for faster wins
        if signal == "BUY":
            sl_price = price - atr; tp_price = price + atr * 1.5
        else:
            sl_price = price + atr; tp_price = price - atr * 1.5

        return {"pair": name, "signal": signal, "price": price, "sl": sl_price, "tp": tp_price, "rsi": rsi, "atr": atr, "adx": adx, "quality": quality, "fvg": has_fvg}
    except Exception as e:
        print(f"Error {name}: {e}")
        return None

def main():
    print("V10 AGGRESSIVE Started")
    send_msg("🔥 *V10 AGGRESSIVE is LIVE*\n\nRisk: *5% per trade*\nTP: *1.5R* (faster wins)\nSession: *09:00-22:00 EAT*\nMax: *3 trades/day*\n\n⚠️ STOP RULE: If balance hits $7, STOP and tell me.\nLot: *0.01 ONLY* | No revenge trades")
    last_signals = {}
    sent_today = {}
    while True:
        try:
            now = now_eat()
            day_key = now.strftime("%Y-%m-%d")
            if day_key not in sent_today: sent_today[day_key] = []
            if len(sent_today[day_key]) >= 3:
                print("Max 3 trades today reached")
                time.sleep(600); continue
            if in_news_block():
                time.sleep(300); continue
            if not in_session():
                time.sleep(60); continue
            for name, ticker in PAIRS.items():
                res = get_signal(name, ticker)
                if not res: continue
                if last_signals.get(name) == res["signal"]: continue
                last_signals[name] = res["signal"]
                sent_today[day_key].append(name)
                fvg_text = "✅ FVG" if res["fvg"] else "BOS only"
                msg = (
                    f"🔥 *V10 AGGRESSIVE SIGNAL {len(sent_today[day_key])}/3*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*Pair:* {res['pair']}\n"
                    f"*Signal:* {res['signal']} {'🟢' if res['signal']=='BUY' else '🔴'}\n"
                    f"*Quality:* {res['quality']}\n"
                    f"*Entry:* `{res['price']:.5f}`\n"
                    f"*Stop Loss:* `{res['sl']:.5f}`\n"
                    f"*Take Profit:* `{res['tp']:.5f}` (1.5R)\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*RSI:* {res['rsi']:.1f} | *ADX:* {res['adx']:.1f}\n"
                    f"*FVG:* {fvg_text}\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*LOT: 0.01 ONLY* | Risk $0.50 | Win $0.75\n"
                    f"⏰ {now.strftime('%H:%M')} EAT\n"
                    f"⚠️ Trade {len(sent_today[day_key])}/3 today - NO MORE AFTER THIS IF 3"
                )
                send_msg(msg)
                print(f"Sent {name} {res['signal']}")
            time.sleep(300)
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
