import os, time, threading, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime
from flask import Flask
import pytz

# ============ V5.1 MANUAL PRO ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
RISK_PCT = 0.01
EAT = pytz.timezone("Africa/Nairobi")

# MANUAL PRO: Only 3 pairs you master
PAIRS = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
}

app = Flask(__name__)
@app.route("/")
def home(): return "V5.1 MANUAL PRO RUNNING"
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
    wd = now.weekday(); hm = now.hour*60 + now.minute
    if wd == 4 and 900 <= hm <= 990: return "NFP"
    if wd == 2 and 900 <= hm <= 990: return "CPI"
    if wd == 2 and 1230 <= hm <= 1320: return "FOMC"
    return None

def calc_lot(pair, sl_pips):
    risk_money = ACCOUNT_BALANCE * RISK_PCT
    if pair == "XAUUSD":
        lot = risk_money / max(sl_pips, 1) / 10
    elif "JPY" in pair:
        lot = risk_money / (sl_pips * 9)
    else:
        lot = risk_money / (sl_pips * 10)
    return round(max(0.01, min(lot, 0.5)), 2)

def analyze_pair(name, ticker):
    now = datetime.now(EAT)
    df15 = get_data(ticker, "15m", "5d")
    df1h = get_data(ticker, "1h", "10d")
    if df15 is None or df1h is None or len(df15) < 60: return None
    # MANUAL PRO: Use LAST CLOSED candle (-2), not forming candle (-1)
    # This is the confirmation candle fix - no fake mid-candle signals
    df = df15.iloc[:-1]  # drop forming candle
    close15 = df['Close']
    price = float(close15.iloc[-1])
    last_closed = df.iloc[-1]
    prev_closed = df.iloc[-2]
    # Confirmation: candle must close in direction of trade
    candle_up = float(last_closed['Close']) > float(last_closed['Open'])
    candle_down = float(last_closed['Close']) < float(last_closed['Open'])
    candle_body = abs(float(last_closed['Close']) - float(last_closed['Open']))
    candle_range = float(last_closed['High']) - float(last_closed['Low'])
    # Body must be at least 40% of range = strong candle, not doji
    if candle_range == 0 or candle_body / candle_range < 0.4:
        return None
    r = float(rsi(close15).iloc[-1])
    m, sig = macd(close15); macd_val = float(m.iloc[-1]); sig_val = float(sig.iloc[-1])
    adx_val, pdi, mdi = adx(df)
    ema50 = float(close15.ewm(span=50, adjust=False).mean().iloc[-1])
    close1h = df1h['Close']
    ema200_1h = float(close1h.ewm(span=200, adjust=False).mean().iloc[-1])
    tr = pd.concat([df['High']-df['Low'], (df['High']-close15.shift()).abs(), (df['Low']-close15.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    sup = float(df1h['Low'].tail(50).min()); res = float(df1h['High'].tail(50).max())
    trend_1h = "UP" if price > ema200_1h else "DOWN"
    if adx_val < 20: return None
    direction = None
    # BUY: needs bullish confirmation candle
    if price > ema50 and 50 < r < 70 and macd_val > sig_val and pdi > mdi and trend_1h == "UP" and candle_up:
        direction = "BUY"
    elif price < ema50 and 30 < r < 50 and macd_val < sig_val and mdi > pdi and trend_1h == "DOWN" and candle_down:
        direction = "SELL"
    else:
        return None
    dist_to_res = abs(res - price) / max(price,1e-9) * 100
    dist_to_sup = abs(price - sup) / max(price,1e-9) * 100
    if direction == "BUY" and dist_to_res < 0.05: return None
    if direction == "SELL" and dist_to_sup < 0.05: return None
    score = 50
    if adx_val > 25: score += 10
    if adx_val > 30: score += 5
    if direction == "BUY" and 55 <= r <= 65: score += 10
    if direction == "SELL" and 35 <= r <= 45: score += 10
    if candle_body / candle_range > 0.6: score += 10  # Very strong candle
    if dist_to_res > 0.2 and direction=="BUY": score += 5
    if dist_to_sup > 0.2 and direction=="SELL": score += 5
    score = min(98, max(55, int(score)))
    grade = "A+ Setup" if score >= 90 else "A Setup" if score >= 80 else "B Setup"
    if direction == "BUY":
        sl = price - atr*1.5; tp1 = price + atr*1.5; tp2 = price + atr*3
    else:
        sl = price + atr*1.5; tp1 = price - atr*1.5; tp2 = price - atr*3
    sl_dist = abs(price - sl)
    if "JPY" in name: sl_pips = sl_dist * 100
    elif name == "XAUUSD": sl_pips = sl_dist * 10
    else: sl_pips = sl_dist * 10000
    lot = calc_lot(name, max(sl_pips,1))
    return {"pair": name, "dir": direction, "price": price, "rsi": r, "adx": adx_val, "trend1h": trend_1h, "score": score, "grade": grade, "sl": sl, "tp1": tp1, "tp2": tp2, "lot": lot, "sup": sup, "res": res}

def in_session(now):
    wd = now.weekday()
    if wd >= 5: return False
    # Block Mon 10-12 fake moves, Fri after 17:00
    if wd == 0 and now.hour < 12: return False
    if wd == 4 and now.hour >= 17: return False
    hm = now.hour*60 + now.minute
    return (600 <= hm <= 840) or (900 <= hm <= 1140)

def get_live_price(ticker):
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1])
    except: return None

def bot_loop():
    sent_today = set()
    active_trades = {}  # pair -> {dir, tp1, tp2, sl, alerted}
    last_report_day = None
    last_bias_day = None
    send("🚀 <b>V5.1 MANUAL PRO is LIVE</b>\n✅ Only 3 Pairs: XAUUSD, EURUSD, GBPJPY\n✅ Confirmation Candle - No Fake Signals\n✅ TP1 Alert - Move SL to Entry!\n✅ 1H Trend + ADX + News Filter\n✅ Mon Morning / Fri Evening Blocked\n💰 Balance: $%.0f\n📖 Manual Mode - You Click, Bot Guides" % ACCOUNT_BALANCE)
    while True:
        try:
            now = datetime.now(EAT)
            # Daily Bias 09:00 EAT
            if now.hour == 9 and now.minute < 5 and last_bias_day != now.date():
                last_bias_day = now.date()
                bias_lines = []
                for n, t in PAIRS.items():
                    d = get_data(t, "1h", "10d")
                    if d is not None:
                        c = d['Close']; e200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1]); p = float(c.iloc[-1])
                        bias_lines.append(f"{n}: {'🟢 BUY ONLY' if p>e200 else '🔴 SELL ONLY'}")
                send(f"🌅 <b>TODAY'S BIAS - {now.strftime('%Y-%m-%d')}</b>\n" + "\n".join(bias_lines) + "\n\nTrade ONLY in bias direction. Max 2 trades. Good luck.")
            # Daily Report 21:00
            if now.hour == 21 and now.minute < 5 and last_report_day != now.date():
                last_report_day = now.date()
                send(f"📊 <b>DAILY REPORT - {now.strftime('%Y-%m-%d')}</b>\nSignals Today: {len(sent_today)}\nBalance: ${ACCOUNT_BALANCE:.0f}\nBot: V5.1 MANUAL PRO ✅")
                sent_today = set(); active_trades = {}
            if not in_session(now):
                time.sleep(60); continue
            if is_news_time(now):
                time.sleep(60); continue
            # Check TP1 for active trades - BREAKEVEN ALERT
            for pair_name, tr in list(active_trades.items()):
                if tr.get("alerted"): continue
                live = get_live_price(PAIRS[pair_name])
                if live is None: continue
                hit = (live >= tr["tp1"]) if tr["dir"]=="BUY" else (live <= tr["tp1"])
                if hit:
                    tr["alerted"] = True
                    send(f"⚠️ <b>TP1 HIT - MOVE SL NOW!</b>\n{pair_name} {tr['dir']}\nPrice hit TP1: {tr['tp1']:.5f}\n👉 In MT5: Move SL to Entry Price NOW!\nThen change TP to TP2: {tr['tp2']:.5f}\nThis trade is now FREE - you cannot lose.")
            # Scan for new signals
            for name, ticker in PAIRS.items():
                key = f"{name}-{now.strftime('%Y-%m-%d-%H')}"
                if key in sent_today: continue
                res = analyze_pair(name, ticker)
                if res:
                    sent_today.add(key)
                    active_trades[name] = {"dir": res['dir'], "tp1": res['tp1'], "tp2": res['tp2'], "sl": res['sl'], "alerted": False}
                    emoji = "🟢" if res['dir']=="BUY" else "🔴"
                    msg = (f"{emoji} <b>{res['pair']} {res['dir']} - V5.1 MANUAL PRO</b>\n"
                           f"⭐ Score: {res['score']}/100 - {res['grade']}\n"
                           f"✅ Confirmed Candle Closed\n"
                           f"Price: {res['price']:.5f}\n"
                           f"RSI: {res['rsi']:.1f} | ADX: {res['adx']:.1f} | 1H: {res['trend1h']}\n"
                           f"Lot: {res['lot']} (Risk 1% = ${ACCOUNT_BALANCE*RISK_PCT:.2f})\n"
                           f"🎯 TP1: {res['tp1']:.5f} (Move SL to Entry here)\n"
                           f"🎯 TP2: {res['tp2']:.5f}\n"
                           f"🛑 SL: {res['sl']:.5f}\n"
                           f"Support: {res['sup']:.5f} | Resistance: {res['res']:.5f}\n"
                           f"⏰ {now.strftime('%H:%M')} EAT\n"
                           f"⚠️ Max 2 trades today. If Score 80+, take it.")
                    send(msg)
                time.sleep(2)
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
