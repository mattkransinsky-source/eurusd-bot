import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
import threading
from flask import Flask

# --- WEB SERVER FOR RENDER (Fixes Port Error) ---
app = Flask(__name__)
@app.route('/')
def home(): return "V11 MONSTER Running"
def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=run_web, daemon=True).start()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
}

# --- MONSTER STATE ---
STATE_FILE = "monster_state.json"
JOURNAL_FILE = "monster_journal.json"

def load_state():
    try:
        with open(STATE_FILE, 'r') as f: return json.load(f)
    except: return {"balance": 10.0, "consecutive_losses": 0, "consecutive_wins": 0, "total_trades": 0, "wins": 0, "losses": 0, "mode": "AGGRESSIVE"}

def save_state(s):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(s, f)
    except: pass

def log_journal(entry):
    try:
        data = []
        try:
            with open(JOURNAL_FILE, 'r') as f: data = json.load(f)
        except: pass
        data.append(entry)
        with open(JOURNAL_FILE, 'w') as f: json.dump(data, f)
    except: pass

def now_eat():
    return datetime.now() + timedelta(hours=3)

def in_killzone():
    now = now_eat()
    if now.weekday() >= 5: return False, "WEEKEND"
    h = now.hour + now.minute/60
    # London: 10:00-12:00 EAT, New York: 15:00-18:00 EAT
    if 10 <= h < 12: return True, "LONDON"
    if 15 <= h < 18: return True, "NEW YORK"
    return False, "OUTSIDE KILLZONE"

def in_news_block():
    now = now_eat()
    # High impact guess + real block: NFP first Friday, CPI Wed
    if now.weekday() == 4 and 15 <= now.hour < 17: return True  # Fri NFP window
    if now.weekday() == 2 and 15 <= now.hour < 16: return True  # Wed CPI window
    return False

def send_msg(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e: print(f"TG error: {e}")
    if DISCORD_WEBHOOK:
        try: requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
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

def get_data(ticker, period, interval):
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if df is None or len(df) < 210: return None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    return df.dropna()

def get_signal(name, ticker, state):
    # --- ADAPTIVE RISK ---
    if state["consecutive_losses"] >= 2:
        risk_pct = 0.02  # Scared mode
        risk_label = "🛡️ PROTECT 2%"
    elif state["consecutive_wins"] >= 2:
        risk_pct = 0.06  # Confident mode
        risk_label = "🔥 CONFIDENT 6%"
    else:
        risk_pct = 0.05
        risk_label = "⚡ AGGRESSIVE 5%"

    if state["balance"] <= 7.0:
        risk_pct = 0.01
        risk_label = "🚨 SAFE MODE 1% - Balance Low"

    df15 = get_data(ticker, "5d", "15m")
    df1h = get_data(ticker, "10d", "1h")
    df4h = get_data(ticker, "1mo", "4h")
    if df15 is None or df1h is None or df4h is None: return None

    price = float(df15['Close'].iloc[-1])

    # --- 4H TREND BOSS ---
    ema50_4h = calc_ema(df4h, 50); ema200_4h = calc_ema(df4h, 200)
    trend_4h = "BULLISH" if price > ema50_4h > ema200_4h else "BEARISH" if price < ema50_4h < ema200_4h else "RANGE"
    if trend_4h == "RANGE": return None

    # --- 1H STRUCTURE BOSS ---
    ema50_1h = calc_ema(df1h, 50)
    bos_up_1h, bos_down_1h, _, _ = check_bos_fvg(df1h)

    # --- 15M ENTRY BOSS ---
    ema50_15 = calc_ema(df15, 50); ema200_15 = calc_ema(df15, 200)
    rsi = calc_rsi(df15['Close']); atr = calc_atr(df15); adx = calc_adx(df15)
    bos_up, bos_down, fvg_bull, fvg_bear = check_bos_fvg(df15)

    signal = None
    reason = ""
    confidence = 0

    # BULLISH MONSTER SETUP - All 3 must agree
    if trend_4h == "BULLISH" and price > ema50_1h and bos_up_1h:
        if price > ema50_15 > ema200_15 and 42 < rsi < 70 and (bos_up or fvg_bull):
            signal = "BUY"
            confidence = 85 + (10 if fvg_bull and bos_up else 0)
            reason = "4H Bull + 1H BOS + 15m FVG/BOS"

    # BEARISH MONSTER SETUP
    if trend_4h == "BEARISH" and price < ema50_1h and bos_down_1h:
        if price < ema50_15 < ema200_15 and 30 < rsi < 58 and (bos_down or fvg_bear):
            signal = "SELL"
            confidence = 85 + (10 if fvg_bear and bos_down else 0)
            reason = "4H Bear + 1H BOS + 15m FVG/BOS"

    if not signal: return None
    if adx < 18: return None  # Monster needs strength

    risk_amt = state["balance"] * risk_pct
    reward_amt = risk_amt * 2  # 2R

    if signal == "BUY":
        sl_price = price - atr; tp_price = price + atr * 2
    else:
        sl_price = price + atr; tp_price = price - atr * 2

    return {"pair": name, "signal": signal, "price": price, "sl": sl_price, "tp": tp_price,
            "rsi": rsi, "adx": adx, "trend_4h": trend_4h, "confidence": min(confidence, 95),
            "reason": reason, "risk_pct": risk_pct, "risk_label": risk_label,
            "risk_amt": risk_amt, "reward_amt": reward_amt}

def main():
    print("V11 MONSTER Started")
    state = load_state()
    send_msg(f"👹 *V11 MONSTER IS LIVE*\n\nKillzones: *London 10-12 | NY 15-18 EAT*\nBrain: *4H + 1H + 15m must agree*\nRisk: *Adaptive 2% - 6%*\nBalance tracked: *${state['balance']:.2f}*\nWins: {state['wins']} | Losses: {state['losses']}\n\nI only trade when big money trades. No more chop.")
    last_signals = {}
    sent_today = {}
    while True:
        try:
            now = now_eat()
            day_key = now.strftime("%Y-%m-%d")
            if day_key not in sent_today: sent_today[day_key] = []
            if len(sent_today[day_key]) >= 2:
                time.sleep(600); continue
            in_kz, kz_name = in_killzone()
            if not in_kz:
                time.sleep(120); continue
            if in_news_block():
                time.sleep(300); continue
            for name, ticker in PAIRS.items():
                res = get_signal(name, ticker, state)
                if not res: continue
                if last_signals.get(name) == res["signal"]: continue
                last_signals[name] = res["signal"]
                sent_today[day_key].append(name)
                log_journal({"time": now.isoformat(), "pair": name, "signal": res["signal"], "price": res["price"], "confidence": res["confidence"], "reason": res["reason"]})
                msg = (
                    f"👹 *V11 MONSTER SIGNAL {len(sent_today[day_key])}/2*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*Pair:* {res['pair']} | *{res['signal']}* {'🟢' if res['signal']=='BUY' else '🔴'}\n"
                    f"*Confidence:* {res['confidence']}% | *Killzone:* {kz_name}\n"
                    f"*Why:* {res['reason']}\n"
                    f"*4H Trend:* {res['trend_4h']}\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*Entry:* `{res['price']:.5f}`\n"
                    f"*SL:* `{res['sl']:.5f}`\n"
                    f"*TP:* `{res['tp']:.5f}` (2R)\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*RSI:* {res['rsi']:.1f} | *ADX:* {res['adx']:.1f}\n"
                    f"*{res['risk_label']}* | Risk ${res['risk_amt']:.2f} | Win ${res['reward_amt']:.2f}\n"
                    f"*LOT: 0.01 ONLY*\n"
                    f"⏰ {now.strftime('%H:%M')} EAT\n"
                    f"⚠️ After you trade, tell me WIN or LOSS so I adapt"
                )
                send_msg(msg)
                print(f"Sent {name} {res['signal']}")
            time.sleep(300)
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
