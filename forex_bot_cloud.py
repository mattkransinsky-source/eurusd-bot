import os, time, threading, requests
import pandas as pd
import numpy as np
from flask import Flask
from datetime import datetime, timezone

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))

SYMBOLS = {"EURUSD": "EURUSD=X", "GBPJPY": "GBPJPY=X", "XAUUSD": "GC=F"}
PAIR_PIP = {"EURUSD": 0.0001, "GBPJPY": 0.01, "XAUUSD": 0.1}
MIN_SL_PIPS = {"EURUSD": 18, "GBPJPY": 30, "XAUUSD": 18}

app = Flask(__name__)
@app.route("/")
def home():
    return "V6.3 STRONGER - A+ ONLY RUNNING"

def send(msg):
    msg = msg.replace("%", " pct").replace("/", " per ")
    try:
        if not TOKEN or not CHAT_ID: return
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=15)
        print(f"send status: {r.status_code}")
    except Exception as e:
        print(f"Send failed: {e}")

def get_data(ysym):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        for base in ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]:
            url = f"{base}/v8/finance/chart/{ysym}?interval=15m&range=5d"
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                j = r.json()
                res = j["chart"]["result"][0]
                ts = res["timestamp"]
                q = res["indicators"]["quote"][0]
                df = pd.DataFrame({"Close": q["close"], "High": q["high"], "Low": q["low"], "Open": q["open"], "Volume": q["volume"]})
                df = df.dropna().reset_index(drop=True)
                if len(df) >= 100:
                    return df
        return None
    except Exception as e:
        print(f"Data error {ysym}: {e}")
        return None

def get_dxy():
    df = get_data("DX-Y.NYB")
    if df is None or len(df) < 5:
        return "NEUTRAL"
    try:
        if df["Close"].iloc[-1] > df["Close"].iloc[-5]: return "BULLISH"
        if df["Close"].iloc[-1] < df["Close"].iloc[-5]: return "BEARISH"
    except: pass
    return "NEUTRAL"

def analyze(name, df):
    try:
        close = df["Close"]; high = df["High"]; low = df["Low"]; open_ = df["Open"]
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean()
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / (loss + 0.0000001))))
        # ATR
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # VWAP fixed - typical price
        typical = (high + low + close) / 3
        vwap = typical.rolling(20).mean()
        # MACD
        ema12 = close.ewm(span=12).mean(); ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26; macd_sig = macd.ewm(span=9).mean()
        # ADX - trend strength
        up = high.diff(); down = -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr_smooth = pd.Series(tr).rolling(14).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / (tr_smooth + 0.0000001))
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / (tr_smooth + 0.0000001))
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 0.0000001))
        adx = dx.rolling(14).mean()

        price = float(close.iloc[-1]); e20 = float(ema20.iloc[-1]); e50 = float(ema50.iloc[-1]); e200 = float(ema200.iloc[-1])
        r = float(rsi.iloc[-1]); a = float(atr.iloc[-1]); v = float(vwap.iloc[-1])
        m = float(macd.iloc[-1]); ms = float(macd_sig.iloc[-1]); adx_v = float(adx.iloc[-1])
        pip = PAIR_PIP[name]
        # ATR volatility filter - must have movement
        atr_pips = a / pip
        if atr_pips < 8: # market too dead
            return None
        # Candle confirmation
        last_candle_bull = float(close.iloc[-1]) > float(open_.iloc[-1])
        last_candle_bear = float(close.iloc[-1]) < float(open_.iloc[-1])

        # 4H trend
        df4 = df.iloc[::16]
        c4 = df4["Close"]; e50_4 = c4.ewm(span=50).mean()
        trend4 = "BULLISH" if float(c4.iloc[-1]) > float(e50_4.iloc[-1]) else "BEARISH"

        dxy = get_dxy()
        if name in ["EURUSD", "XAUUSD"]:
            dxy_buy = dxy != "BULLISH"; dxy_sell = dxy != "BEARISH"
        else:
            dxy_buy = True; dxy_sell = True

        # STRONGER: 5/5 REQUIRED + ADX + MACD + Candle
        buy_conditions = [
            e20 > e50 and e50 > e200,
            trend4 == "BULLISH",
            45 < r < 68,
            price > v,
            m > ms and adx_v > 25 and last_candle_bull
        ]
        sell_conditions = [
            e20 < e50 and e50 < e200,
            trend4 == "BEARISH",
            32 < r < 55,
            price < v,
            m < ms and adx_v > 25 and last_candle_bear
        ]
        buy_score = sum(buy_conditions)
        sell_score = sum(sell_conditions)

        direction = None
        if buy_score == 5 and dxy_buy: direction = "BUY"
        elif sell_score == 5 and dxy_sell: direction = "SELL"
        else: return None

        # SL TP - ATR 1.5x but min SL
        sl_pips = max((a * 1.5) / pip, MIN_SL_PIPS[name])
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

        msg = (f"STRONGER SIGNAL - {name} {direction} - A+ SETUP\n"
               f"Price: {price:.5f}\n"
               f"Trend 4H: {trend4} | DXY: {dxy}\n"
               f"EMA20: {e20:.5f} EMA50: {e50:.5f} EMA200: {e200:.5f}\n"
               f"RSI: {r:.1f} | ADX: {adx_v:.1f} | MACD: {m:.5f}\n"
               f"VWAP: {v:.5f} | ATR: {atr_pips:.1f} pips\n"
               f"Entry: {price:.5f}\n"
               f"SL: {sl:.5f} ({sl_pips:.1f} pips)\n"
               f"TP1: {tp1:.5f}\nTP2: {tp2:.5f}\nTP3: {tp3:.5f}\n"
               f"Lot: {lot} | Risk: 5 pct | RR 1 to 3\n"
               f"Score: 5 per 5 - A+ ONLY")
        return msg
    except Exception as e:
        print(f"Analyze error {name}: {e}")
        return None

def bot_loop():
    time.sleep(5)
    send("V6.3 STRONGER is LIVE - A+ ONLY\n5 per 5 Required - ADX over 25 - MACD Confirm - Candle Confirm - ATR Filter - 1 Trade Per 2 Hours\nNo Weak Signals Anymore")
    last_signal_hour = -99
    while True:
        try:
            now = datetime.now(timezone.utc)
            if 7 <= now.hour < 20:
                # 1 trade per 2 hours
                if abs(now.hour - last_signal_hour) >= 2:
                    for name, ysym in SYMBOLS.items():
                        df = get_data(ysym)
                        if df is None: continue
                        sig = analyze(name, df)
                        if sig:
                            send(sig)
                            last_signal_hour = now.hour
                            break
                        time.sleep(3)
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(30)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
