import os, time, threading, requests
import pandas as pd
import numpy as np
from flask import Flask
from datetime import datetime, timezone

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))

# FIXED: Gold is GC=F, not XAUUSD=X (delisted)
SYMBOLS = {"EURUSD": "EURUSD=X", "GBPJPY": "GBPJPY=X", "XAUUSD": "GC=F"}
PAIR_PIP = {"EURUSD": 0.0001, "GBPJPY": 0.01, "XAUUSD": 0.1}
MIN_SL_PIPS = {"EURUSD": 18, "GBPJPY": 30, "XAUUSD": 18} # XAUUSD 18 = $1.80

app = Flask(__name__)
@app.route("/")
def home():
    return "V6.2 FINAL BOSS RUNNING - Yahoo Direct API"

def send(msg):
    msg = msg.replace("%", " pct").replace("/", " per ")
    try:
        if not TOKEN or not CHAT_ID: return
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=15)
        print(f"send status: {r.status_code}")
    except Exception as e:
        print(f"Send failed: {e}")

def get_data(yahoo_symbol):
    # Direct Yahoo v8 API - bypasses yfinance crumb / 401 block
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=15m&range=5d"
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"Yahoo {yahoo_symbol} status {r.status_code}")
            # Try query2 fallback
            url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=15m&range=5d"
            r = requests.get(url2, headers=headers, timeout=20)
            if r.status_code != 200:
                return None
        j = r.json()
        res = j["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({"Close": q["close"], "High": q["high"], "Low": q["low"], "Open": q["open"], "Volume": q["volume"]})
        df["Date"] = pd.to_datetime(ts, unit="s")
        df = df.dropna().reset_index(drop=True)
        if len(df) < 100:
            print(f"Not enough data {yahoo_symbol}: {len(df)}")
            return None
        return df
    except Exception as e:
        print(f"Data error {yahoo_symbol}: {e}")
        return None

def get_dxy():
    df = get_data("DX-Y.NYB")
    if df is None or len(df) < 5:
        return "NEUTRAL"
    try:
        if df["Close"].iloc[-1] > df["Close"].iloc[-5]:
            return "BULLISH"
        if df["Close"].iloc[-1] < df["Close"].iloc[-5]:
            return "BEARISH"
    except: pass
    return "NEUTRAL"

def analyze(name, df):
    try:
        close = df["Close"]; high = df["High"]; low = df["Low"]
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean()
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / (loss + 0.0000001))))
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # FIXED VWAP: no volume needed, use typical price SMA
        typical = (high + low + close) / 3
        vwap = typical.rolling(20).mean()

        price = float(close.iloc[-1]); e20 = float(ema20.iloc[-1]); e50 = float(ema50.iloc[-1]); e200 = float(ema200.iloc[-1])
        r = float(rsi.iloc[-1]); a = float(atr.iloc[-1]); v = float(vwap.iloc[-1])
        pip = PAIR_PIP[name]

        # 4H trend
        df4 = df.copy()
        df4 = df4.iloc[::16] # approx 4H from 15m
        c4 = df4["Close"]; e50_4 = c4.ewm(span=50).mean()
        trend4 = "BULLISH" if float(c4.iloc[-1]) > float(e50_4.iloc[-1]) else "BEARISH"

        dxy = get_dxy()
        if name == "EURUSD":
            dxy_buy = dxy != "BULLISH"; dxy_sell = dxy != "BEARISH"
        elif name == "XAUUSD":
            dxy_buy = dxy != "BULLISH"; dxy_sell = dxy != "BEARISH"
        else:
            dxy_buy = True; dxy_sell = True

        buy = sum([e20>e50, e50>e200, trend4=="BULLISH", 40<r<70, price>v])
        sell = sum([e20<e50, e50<e200, trend4=="BEARISH", 30<r<60, price<v])

        direction = None
        if buy >= 4 and dxy_buy: direction = "BUY"
        elif sell >= 4 and dxy_sell: direction = "SELL"
        else: return None

        # FIXED SL: ATR * 1.5 but NEVER less than MIN_SL
        atr_pips = (a * 1.5) / pip
        sl_pips = max(atr_pips, MIN_SL_PIPS[name])
        sl_dist = sl_pips * pip
        if direction == "BUY":
            sl = price - sl_dist; tp1 = price + sl_dist; tp2 = price + sl_dist*2; tp3 = price + sl_dist*3
        else:
            sl = price + sl_dist; tp1 = price - sl_dist; tp2 = price - sl_dist*2; tp3 = price - sl_dist*3

        risk_money = BALANCE * 0.05
        if name == "XAUUSD":
            lot = round(risk_money / (sl_pips * 1.0), 2)
        else:
            lot = round(risk_money / (sl_pips * 10), 2)
        lot = max(0.01, min(lot, 5.0))

        msg = (f"GOLD SIGNAL - {name} {direction}\n"
               f"Price: {price:.5f}\n"
               f"Trend 4H: {trend4} | DXY: {dxy}\n"
               f"EMA20: {e20:.5f} EMA50: {e50:.5f}\n"
               f"RSI: {r:.1f} | VWAP: {v:.5f}\n"
               f"Entry: {price:.5f}\n"
               f"SL: {sl:.5f} ({sl_pips:.1f} pips)\n"
               f"TP1: {tp1:.5f}\nTP2: {tp2:.5f}\nTP3: {tp3:.5f}\n"
               f"Lot: {lot} | Risk: 5 pct | RR 1 to 3\n"
               f"Score: {buy if direction=='BUY' else sell} per 5")
        return msg
    except Exception as e:
        print(f"Analyze error {name}: {e}")
        return None

def bot_loop():
    time.sleep(5)
    send("V6.2 FINAL BOSS is LIVE - Yahoo Direct API Fixed\nPairs: XAUUSD (GC=F), EURUSD, GBPJPY\nFixes: VWAP Fixed, Min SL Fixed, 1 Trade Per Hour, No yfinance 401 Block")
    last_signal_hour = -1
    while True:
        try:
            now = datetime.now(timezone.utc)
            if 7 <= now.hour < 20:
                # 1 TRADE PER HOUR BLOCK
                if now.hour != last_signal_hour:
                    for name, ysym in SYMBOLS.items():
                        df = get_data(ysym)
                        if df is None: continue
                        sig = analyze(name, df)
                        if sig:
                            send(sig)
                            last_signal_hour = now.hour
                            break # stop after 1 signal
                        time.sleep(3)
                else:
                    print(f"Already sent signal this hour {now.hour}, waiting")
            else:
                print(f"Outside session {now.hour} UTC")
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(30)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
