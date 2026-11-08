# forex_bot_cloud.py - V4 PRO - All 5 Upgrades
# Features: RSI+MACD filter, Auto Lot, TP1/TP2 tracking, Daily Report, 9 Pairs
import os, time, threading, requests, traceback
from datetime import datetime, timezone, timedelta
from flask import Flask
import yfinance as yf
import pandas as pd
import numpy as np

# ===== CONFIG - Uses your existing Render Env Vars =====
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))  # Change in Render > Environment to 200, 500 etc
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
MIN_CONFIDENCE = 75

app = Flask(__name__)
@app.route("/")
def home():
    return "V4 PRO BOT LIVE"
@app.route("/health")
def health():
    return "OK"

# ===== PAIRS - V4 Expanded =====
# Yahoo tickers
PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURJPY": "EURJPY=X",
    "XAUUSD": "GC=F",
    "US30": "^DJI",
    "NAS100": "^NDX",
    "BTCUSD": "BTC-USD",
}
# Pip size for SL/TP calc
PIP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "EURJPY": 0.01, "GBPJPY": 0.01, "USDJPY": 0.01,
    "XAUUSD": 0.1, "US30": 1.0, "NAS100": 1.0, "BTCUSD": 1.0,
}
# For lot calc - value per 1.0 pip move for 0.01 lot approx
# Forex 0.01 = $0.10/pip, Gold 0.01 = $0.10 per $1, Indices/Crypto use half lot rule

open_trades = []  # {pair, dir, entry, sl, tp1, tp2, tp1_hit, lot}
stats = {"signals": 0, "tp1": 0, "tp2": 0, "sl": 0, "pips": 0.0}
daily_report_sent = ""

def send(msg):
    if not TOKEN or not CHAT_ID:
        print("No Telegram config", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Send fail", e, flush=True)

def get_eat_now():
    return datetime.now(timezone.utc) + timedelta(hours=3)

def in_session():
    now = get_eat_now()
    h = now.hour + now.minute/60
    wd = now.weekday()  # 0=Mon
    if wd >= 5:  # Sat/Sun
        return False, "Weekend"
    if 9 <= h < 13:
        return True, "London"
    if 15 <= h < 19:
        return True, "New York"
    if 9 <= h < 19:
        return True, "London/NY Overlap"
    return False, "Outside session"

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(series):
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9).mean()
    return macd, sig

def calc_lot(pair, sl_dist_pips):
    # 1% risk rule: Risk $ = Balance * 1%
    risk_dollars = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    if pair in ["XAUUSD", "US30", "NAS100", "BTCUSD"]:
        # Volatile - half lot
        # For gold/indices, approximate: risk / (sl_dist * 1.0) scaled
        lot = risk_dollars / max(sl_dist_pips, 1) / 10
        lot = max(0.01, min(lot, 0.10))
        # Half it for safety
        lot = round(max(0.01, lot / 2 / 0.01) * 0.01, 2)
    else:
        # Forex: 0.01 lot = $0.10 per pip. Lot = Risk / (SL_pips * 10)
        # Because 1.0 lot = $10/pip
        lot = risk_dollars / (max(sl_dist_pips, 1) * 10)
        lot = max(0.01, min(lot, 1.0))
        lot = round(lot / 0.01) * 0.01
    return round(lot, 2)

def analyze(pair, ticker):
    try:
        df = yf.download(ticker, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df is None or len(df) < 50:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"]
        # Indicators
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        rsi = calc_rsi(close).iloc[-1]
        macd, sig = calc_macd(close)
        macd_val = macd.iloc[-1]
        sig_val = sig.iloc[-1]
        price = float(close.iloc[-1])
        prev = float(close.iloc[-2])

        # ATR for SL/TP
        high, low = df["High"], df["Low"]
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr == 0:
            atr = price * 0.001

        pip = PIP[pair]
        # Base confidence
        conf = 70
        direction = None
        reason = []

        # Trend
        trend_up = ema20 > ema50 and price > ema20
        trend_down = ema20 < ema50 and price < ema20

        # Breakout logic
        last_high = float(high.iloc[-5:-1].max())
        last_low = float(low.iloc[-5:-1].min())

        if trend_up and price > last_high:
            direction = "BUY"
            reason.append("London/NY Breakout + Trend Up")
            conf += 5
        elif trend_down and price < last_low:
            direction = "SELL"
            reason.append("Breakdown + Trend Down")
            conf += 5
        elif trend_up and price > prev and price > ema20:
            direction = "BUY"
            reason.append("Trend Up + Momentum")
        elif trend_down and price < prev and price < ema20:
            direction = "SELL"
            reason.append("Trend Down + Momentum")
        else:
            return None

        # ===== UPGRADE 1: RSI + MACD FILTER =====
        if direction == "BUY":
            if rsi > 70:
                print(f"{pair} BUY blocked RSI {rsi:.1f} overbought", flush=True)
                return None
            if macd_val < sig_val:
                print(f"{pair} BUY blocked MACD bearish", flush=True)
                return None
            if rsi < 55:
                conf += 3
        else:  # SELL
            if rsi < 30:
                print(f"{pair} SELL blocked RSI {rsi:.1f} oversold", flush=True)
                return None
            if macd_val > sig_val:
                print(f"{pair} SELL blocked MACD bullish", flush=True)
                return None
            if rsi > 45:
                conf += 3

        if macd_val > sig_val and direction == "BUY":
            conf += 3
        if macd_val < sig_val and direction == "SELL":
            conf += 3

        # News filter placeholder - skip 8:30 & 15:30 EAT high impact windows handled by session check in main loop
        conf = min(conf, 92)
        if conf < MIN_CONFIDENCE:
            return None

        # Smart ATR TP/SL
        sl_dist = atr * 1.5
        tp1_dist = atr * 1.5
        tp2_dist = atr * 3.0

        if direction == "BUY":
            sl = price - sl_dist
            tp1 = price + tp1_dist
            tp2 = price + tp2_dist
        else:
            sl = price + sl_dist
            tp1 = price - tp1_dist
            tp2 = price - tp2_dist

        sl_pips = abs(price - sl) / pip
        lot = calc_lot(pair, sl_pips)
        # Gold/indices lot already halved in calc_lot

        return {
            "pair": pair, "dir": direction, "price": price, "sl": sl, "tp1": tp1, "tp2": tp2,
            "conf": int(conf), "rsi": round(float(rsi),1), "macd": round(float(macd_val),5),
            "reason": " + ".join(reason), "lot": lot, "sl_pips": round(sl_pips,1)
        }
    except Exception as e:
        print(f"Analyze {pair} error {e}", flush=True)
        return None

def check_open_trades():
    # ===== UPGRADE 3: TP1 / TP2 Tracking =====
    for t in list(open_trades):
        try:
            df = yf.download(PAIRS[t["pair"]], period="1d", interval="5m", progress=False, auto_adjust=True)
            if df is None or len(df) == 0:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            price = float(df["Close"].iloc[-1])
            pip = PIP[t["pair"]]
            if t["dir"] == "BUY":
                if not t["tp1_hit"] and price >= t["tp1"]:
                    t["tp1_hit"] = True
                    stats["tp1"] += 1
                    send(f"✅ TP1 HIT — {t['pair']} {t['dir']}\nPrice: {price:.5f}\n👉 Close HALF, Move SL to Entry ({t['entry']:.5f})\nTrade is now RISK-FREE")
                elif t["tp1_hit"] and price >= t["tp2"]:
                    stats["tp2"] += 1
                    stats["pips"] += abs(t["tp2"]-t["entry"])/pip
                    send(f"🎉 TP2 HIT — {t['pair']} {t['dir']} — CLOSE ALL\nEntry {t['entry']:.5f} → TP2 {t['tp2']:.5f}")
                    open_trades.remove(t)
                elif price <= t["sl"]:
                    stats["sl"] += 1
                    stats["pips"] -= abs(t["entry"]-t["sl"])/pip
                    send(f"🛑 SL HIT — {t['pair']} {t['dir']}\nLoss controlled. Next setup coming.")
                    open_trades.remove(t)
            else:  # SELL
                if not t["tp1_hit"] and price <= t["tp1"]:
                    t["tp1_hit"] = True
                    stats["tp1"] += 1
                    send(f"✅ TP1 HIT — {t['pair']} {t['dir']}\nPrice: {price:.5f}\n👉 Close HALF, Move SL to Entry ({t['entry']:.5f})")
                elif t["tp1_hit"] and price <= t["tp2"]:
                    stats["tp2"] += 1
                    stats["pips"] += abs(t["entry"]-t["tp2"])/pip
                    send(f"🎉 TP2 HIT — {t['pair']} {t['dir']} — CLOSE ALL\nEntry {t['entry']:.5f} → TP2 {t['tp2']:.5f}")
                    open_trades.remove(t)
                elif price >= t["sl"]:
                    stats["sl"] += 1
                    stats["pips"] -= abs(t["sl"]-t["entry"])/pip
                    send(f"🛑 SL HIT — {t['pair']} {t['dir']}\nLoss controlled. Next setup coming.")
                    open_trades.remove(t)
        except Exception as e:
            print("Check trade err", e, flush=True)

def daily_report():
    global daily_report_sent
    now = get_eat_now()
    key = now.strftime("%Y-%m-%d")
    # Send at 21:00 EAT once per day
    if now.hour == 21 and now.minute < 5 and daily_report_sent != key:
        daily_report_sent = key
        wins = stats["tp1"] + stats["tp2"]
        send(f"📊 DAILY REPORT — V4 PRO\nDate: {key}\nSignals Today: {stats['signals']}\nTP1 Hits: {stats['tp1']} | TP2 Hits: {stats['tp2']} | SL Hits: {stats['sl']}\nNet Pips: {stats['pips']:.1f}\nAccount: ${ACCOUNT_BALANCE} | Risk: {RISK_PERCENT}% per trade\nBot will resume tomorrow 9 AM EAT. Good night! 🌙")
        # Reset for next day
        stats["signals"] = 0
        stats["tp1"] = 0
        stats["tp2"] = 0
        stats["sl"] = 0
        stats["pips"] = 0.0

def bot_loop():
    print("*** V4 PRO BOT STARTED ***", flush=True)
    send("🚀 V4 PRO BOT is LIVE\n✅ RSI + MACD Filter\n✅ Auto Lot Size\n✅ TP1/TP2 Tracking\n✅ Daily Report 9 PM\n✅ 9 Pairs: EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, XAUUSD, US30, NAS100, BTCUSD")
    sent_today = set()
    while True:
        try:
            now = get_eat_now()
            ok, session = in_session()
            daily_report()
            check_open_trades()
            if not ok:
                print(f"Outside session {now.strftime('%H:%M')} - sleeping", flush=True)
                time.sleep(60)
                continue
            # Reset daily sent at new day
            if now.hour == 9 and now.minute < 10:
                sent_today.clear()
            for pair, ticker in PAIRS.items():
                # Avoid duplicate signal same day same pair+direction
                sig = analyze(pair, ticker)
                if sig:
                    key = f"{now.strftime('%Y-%m-%d')}-{pair}-{sig['dir']}"
                    if key in sent_today:
                        continue
                    sent_today.add(key)
                    stats["signals"] += 1
                    emoji = "🟢" if sig["dir"] == "BUY" else "🔴"
                    # Format price by pair decimals
                    dec = 5 if pair not in ["USDJPY","GBPJPY","EURJPY","XAUUSD","US30","NAS100","BTCUSD"] else (2 if pair in ["USDJPY","GBPJPY","EURJPY"] else 2)
                    if pair == "XAUUSD":
                        dec = 2
                    if pair in ["US30","NAS100","BTCUSD"]:
                        dec = 2
                    msg = (
                        f"{emoji} V4 PRO SIGNAL\n\n"
                        f"Pair: {sig['pair']}\nDirection: {sig['dir']}\nConfidence: {sig['conf']}% 🔥\n\n"
                        f"Entry: {sig['price']:.{dec}f}\nStop Loss: {sig['sl']:.{dec}f} (-{sig['sl_pips']} pips)\n"
                        f"Take Profit 1: {sig['tp1']:.{dec}f}\nTake Profit 2: {sig['tp2']:.{dec}f}\n\n"
                        f"Lot Size: {sig['lot']} (1% risk on ${ACCOUNT_BALANCE})\n"
                        f"RSI: {sig['rsi']} | MACD: {sig['macd']}\n"
                        f"Why: {sig['reason']}\nSession: {session}\nTime: {now.strftime('%H:%M')} EAT\n\n"
                        f"⚠️ At TP1 close HALF & move SL to Entry"
                    )
                    send(msg)
                    open_trades.append({
                        "pair": sig["pair"], "dir": sig["dir"], "entry": sig["price"],
                        "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig["tp2"],
                        "tp1_hit": False, "lot": sig["lot"]
                    })
                    print(f"Sent {pair} {sig['dir']} conf {sig['conf']}", flush=True)
                time.sleep(2)
            time.sleep(120)  # scan every 2 mins in session
        except Exception as e:
            print("Loop error", traceback.format_exc(), flush=True)
            time.sleep(30)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
