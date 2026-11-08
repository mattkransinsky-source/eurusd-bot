import os, time, threading, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta
from flask import Flask
import pytz

# ============ CONFIG ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
RISK_PCT = 0.01
EAT = pytz.timezone("Africa/Nairobi")

PAIRS = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X", "EURJPY": "EURJPY=X", "XAUUSD": "GC=F",
    "US30": "^DJI", "NAS100": "^IXIC", "BTCUSD": "BTC-USD"
}

app = Flask(__name__)
@app.route("/")
def home(): return "V5 ELITE BOT RUNNING"
@app.route("/health")
def health(): return "OK"

def send(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

def rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/p, adjust=False).mean(); al = l.ewm(alpha=1/p, adjust=False).mean()
    rs = ag / (al.replace(0, 1e-9))
    return 100 - (100/(1+rs))

def macd(s):
    e12 = s.ewm(span=12, adjust=False).mean(); e26 = s.ewm(span=26, adjust=False).mean()
    m = e12 - e26; sig = m.ewm(span=9, adjust=False).mean()
    return m, sig

def adx(df, p=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/p, adjust=False).mean()
    up = h - h.shift(); dn = l.shift() - l
    plus = np.where((up>dn)&(up>0), up, 0); minus = np.where((dn>up)&(dn>0), dn, 0)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1/p, adjust=False).mean() / atr.replace(0,1e-9)
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1/p, adjust=False).mean() / atr.replace(0,1e-9)
    dx = 100 * (pdi-mdi).abs() / (pdi+mdi).replace(0,1e-9)
    return dx.ewm(alpha=1/p, adjust=False).mean().iloc[-1], pdi.iloc[-1], mdi.iloc[-1]

def get_data(ticker, interval, period="5d"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df
    except: return None

def is_news_time(now):
    # Block NFP: First Friday 15:30 EAT, CPI: Wed ~15:30, FOMC: Wed 21:00 EAT approx
    # Simple protection: Block 15:00-16:30 on Fri (NFP week) and 15:00-16:30 Wed, 20:30-22:00 Wed
    wd = now.weekday() # 0=Mon
    hm = now.hour*60 + now.minute
    if wd == 4 and 900 <= hm <= 990: return "NFP Window - Blocked"
    if wd == 2 and 900 <= hm <= 990: return "CPI Window - Blocked"
    if wd == 2 and 1230 <= hm <= 1320: return "FOMC Window - Blocked"
    return None

def calc_lot(pair, sl_pips, price):
    risk_money = ACCOUNT_BALANCE * RISK_PCT
    if pair in ["XAUUSD","BTCUSD","US30","NAS100"]:
        # For gold/indices, use ATR based dollar risk approx
        lot = risk_money / max(sl_pips, 1) / 10
    else:
        # Forex: $10 per pip per 1.0 lot
        pip_value_per_lot = 10 if "JPY" not in pair else 9
        lot = risk_money / (sl_pips * pip_value_per_lot)
    lot = max(0.01, min(round(lot, 2), 0.5))
    # Round to 0.01 steps
    return round(lot, 2)

def analyze_pair(name, ticker):
    now = datetime.now(EAT)
    df15 = get_data(ticker, "15m", "5d")
    df1h = get_data(ticker, "1h", "10d")
    if df15 is None or df1h is None or len(df15) < 50: return None
    close15 = df15['Close']; close1h = df1h['Close']
    price = float(close15.iloc[-1])
    # Indicators 15m
    r = float(rsi(close15).iloc[-1])
    m, sig = macd(close15); macd_val = float(m.iloc[-1]); sig_val = float(sig.iloc[-1])
    adx_val, pdi, mdi = adx(df15)
    ema50 = float(close15.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200_1h = float(close1h.ewm(span=200, adjust=False).mean().iloc[-1])
    # ATR for SL
    tr = pd.concat([df15['High']-df15['Low'], (df15['High']-close15.shift()).abs(), (df15['Low']-close15.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    # Support / Resistance from 1H last 50 bars
    sup = float(df1h['Low'].tail(50).min()); res = float(df1h['High'].tail(50).max())
    # Trend 1H
    trend_1h = "UP" if price > ema200_1h else "DOWN"
    # ADX Filter - No trade in ranging
    if adx_val < 20:
        return None
    # Determine direction
    direction = None; reasons = []
    score = 50
    # BUY logic
    if price > ema50 and r > 50 and r < 70 and macd_val > sig_val and pdi > mdi and trend_1h == "UP":
        direction = "BUY"
    elif price < ema50 and r < 50 and r > 30 and macd_val < sig_val and mdi > pdi and trend_1h == "DOWN":
        direction = "SELL"
    else:
        return None
    # S/R Filter - Don't buy into resistance, don't sell into support
    dist_to_res = abs(res - price) / max(price,1e-9) * 100
    dist_to_sup = abs(price - sup) / max(price,1e-9) * 100
    if direction == "BUY" and dist_to_res < 0.05:
        return None
    if direction == "SELL" and dist_to_sup < 0.05:
        return None
    # Score
    if adx_val > 25: score += 10
    if adx_val > 30: score += 5
    if abs(macd_val - sig_val) > 0: score += 5
    if direction == "BUY" and 55 <= r <= 65: score += 10
    if direction == "SELL" and 35 <= r <= 45: score += 10
    if dist_to_res > 0.2 and direction=="BUY": score += 5
    if dist_to_sup > 0.2 and direction=="SELL": score += 5
    score = min(98, max(55, int(score)))
    grade = "A+ Setup" if score >= 90 else "A Setup" if score >= 80 else "B Setup"
    # SL / TP
    if direction == "BUY":
        sl = price - atr*1.5; tp1 = price + atr*1.5; tp2 = price + atr*3
    else:
        sl = price + atr*1.5; tp1 = price - atr*1.5; tp2 = price - atr*3
    # Pips approx
    sl_dist = abs(price - sl)
    if "JPY" in name: sl_pips = sl_dist * 100
    elif name == "XAUUSD": sl_pips = sl_dist * 10
    elif name in ["US30","NAS100","BTCUSD"]: sl_pips = sl_dist
    else: sl_pips = sl_dist * 10000
    lot = calc_lot(name, max(sl_pips,1), price)
    return {
        "pair": name, "dir": direction, "price": price, "rsi": r, "macd": macd_val,
        "adx": adx_val, "trend1h": trend_1h, "score": score, "grade": grade,
        "sl": sl, "tp1": tp1, "tp2": tp2, "lot": lot, "sup": sup, "res": res
    }

def in_session(now):
    wd = now.weekday()
    if wd >= 5: return False
    hm = now.hour*60 + now.minute
    # London 10:00-14:00 EAT, NY 15:00-19:00 EAT
    return (600 <= hm <= 840) or (900 <= hm <= 1140)

def bot_loop():
    sent_today = set()
    last_report_day = None
    send("🚀 <b>V5 ELITE BOT is LIVE</b>\n✅ Multi-Timeframe 1H + 15m\n✅ ADX Ranging Filter\n✅ News Filter (NFP/CPI/FOMC)\n✅ Support / Resistance\n✅ Quality Score A+ / A / B\n✅ Auto Lot Size\n✅ 9 Pairs\n💰 Balance: $%.0f" % ACCOUNT_BALANCE)
    while True:
        try:
            now = datetime.now(EAT)
            # Daily Report 21:00 EAT
            if now.hour == 21 and now.minute < 5 and last_report_day != now.date():
                last_report_day = now.date()
                send(f"📊 <b>DAILY REPORT - {now.strftime('%Y-%m-%d')}</b>\nSignals Today: {len(sent_today)}\nBalance: ${ACCOUNT_BALANCE:.0f}\nBot: V5 ELITE Running ✅\nTomorrow 10:00 EAT we scan again.")
                sent_today = set()
            if not in_session(now):
                time.sleep(60); continue
            news = is_news_time(now)
            if news:
                time.sleep(60); continue
            for name, ticker in PAIRS.items():
                key = f"{name}-{now.strftime('%Y-%m-%d-%H')}"
                if key in sent_today: continue
                res = analyze_pair(name, ticker)
                if res:
                    sent_today.add(key)
                    emoji = "🟢" if res['dir']=="BUY" else "🔴"
                    msg = (f"{emoji} <b>{res['pair']} {res['dir']} - V5 ELITE</b>\n"
                           f"⭐ Score: {res['score']}/100 - {res['grade']}\n"
                           f"Price: {res['price']:.5f}\n"
                           f"RSI: {res['rsi']:.1f} | ADX: {res['adx']:.1f} | 1H Trend: {res['trend1h']}\n"
                           f"Lot Size: {res['lot']} (Risk 1% = ${ACCOUNT_BALANCE*RISK_PCT:.2f})\n"
                           f"🎯 TP1: {res['tp1']:.5f}\n"
                           f"🎯 TP2: {res['tp2']:.5f}\n"
                           f"🛑 SL: {res['sl']:.5f}\n"
                           f"Support: {res['sup']:.5f} | Resistance: {res['res']:.5f}\n"
                           f"⏰ {now.strftime('%H:%M')} EAT | TP1 = Move SL to Entry")
                    send(msg)
                time.sleep(2)
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
