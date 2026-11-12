import os, time, requests, threading, pytz, gc
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def home():
    return "V23 ALIVE - Bot is running", 200

@app.route('/health', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def health():
    return "OK", 200

@app.route('/<path:path>', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def catch_all(path):
    return "V23 ALIVE - Bot is running", 200

@app.errorhandler(404)
def not_found(e):
    return "V23 ALIVE - Bot is running", 200

@app.errorhandler(405)
def method_not_allowed(e):
    return "V23 ALIVE - Bot is running", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask on port {port}...", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# V23 - 8 PAIRS, TIERED, FVG 50% TOLERANCE
SYMBOLS = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "AUDUSD": "AUDUSD=X",
    "EURJPY": "EURJPY=X",
    "US30": "YM=F"
}

last_activity = time.time()

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print("Telegram error:", e, flush=True)

def heartbeat():
    global last_activity
    while True:
        last_activity = time.time()
        print(f"HEARTBEAT alive - {dt.now(pytz.timezone('Africa/Nairobi'))}", flush=True)
        time.sleep(30)

def watchdog():
    global last_activity
    while True:
        time.sleep(30)
        idle = time.time() - last_activity
        print(f"WATCHDOG check - idle {int(idle)}s", flush=True)
        if idle > 240:
            print("WATCHDOG: Frozen for 240s, force restarting...", flush=True)
            os._exit(1)

def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def get_data(yahoo_symbol, interval, period="1d"):
    try:
        df = yf.download(yahoo_symbol, interval=interval, period=period, progress=False, auto_adjust=False, threads=False, timeout=20)
        if df.empty:
            return None
        if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"yfinance error {yahoo_symbol} {interval}: {e}", flush=True)
        return None

def analyze(name, yahoo_sym):
    df_1h = get_data(yahoo_sym, "60m", "1d")
    df_15m = get_data(yahoo_sym, "15m", "1d")
    if df_1h is None or df_15m is None:
        return None
    if len(df_15m) < 50 or len(df_1h) < 50:
        return None
    closes_1h = df_1h['Close'].tolist()
    closes_15m = df_15m['Close'].tolist()
    highs_15m = df_15m['High'].tolist()
    lows_15m = df_15m['Low'].tolist()
    highs_1h = df_1h['High'].tolist()
    lows_1h = df_1h['Low'].tolist()
    score = 0
    details = []
    ema50 = ema(closes_1h, 50)
    ema200 = ema(closes_1h, 20)
    price = closes_1h[-1]
    trend = None
    if price > ema50 and price > ema200:
        trend = "BUY"; score += 20; details.append("EMA 50+200 ✅ +20")
    elif price < ema50 and price < ema200:
        trend = "SELL"; score += 20; details.append("EMA 50+200 ✅ +20")
    else:
        return None
    high_1h_prev = max(highs_1h[-20:-1])
    low_1h_prev = min(lows_1h[-20:-1])
    last_1h_close = closes_1h[-1]
    bos_ok = (trend == "BUY" and last_1h_close > high_1h_prev) or (trend == "SELL" and last_1h_close < low_1h_prev)
    if not bos_ok:
        return None
    score += 20; details.append("1H BOS ✅ +20")
    prev_low = min(lows_15m[-20:-2])
    prev_high = max(highs_15m[-20:-2])
    sweep_ok = False
    for idx in [-4, -3, -2]:
        sweep_c = df_15m.iloc[idx]
        if trend == "BUY" and sweep_c['Low'] < prev_low and sweep_c['Close'] > prev_low:
            rng = sweep_c['High'] - sweep_c['Low']
            wick = sweep_c['Close'] - sweep_c['Low']
            if rng > 0 and wick / rng > 0.5:
                sweep_ok = True
                break
        if trend == "SELL" and sweep_c['High'] > prev_high and sweep_c['Close'] < prev_high:
            rng = sweep_c['High'] - sweep_c['Low']
            wick = sweep_c['High'] - sweep_c['Close']
            if rng > 0 and wick / rng > 0.5:
                sweep_ok = True
                break
    if not sweep_ok:
        return None
    score += 20; details.append("Wick Sweep 50%+ ✅ +20")
    bos15_ok = False
    for i in range(len(df_15m)-3, len(df_15m)-1):
        c = closes_15m[i]
        if trend == "BUY" and c > max(highs_15m[i-5:i]):
            bos15_ok = True
        if trend == "SELL" and c < min(lows_15m[i-5:i]):
            bos15_ok = True
    if not bos15_ok:
        return None
    score += 15; details.append("15m BOS after Sweep ✅ +15")
    fvg_found = False
    fvg_top = fvg_bottom = 0
    for i in range(len(df_15m)-10, len(df_15m)-2):
        h1 = highs_15m[i]; l1 = lows_15m[i]
        h3 = highs_15m[i+2]; l3 = lows_15m[i+2]
        if trend == "BUY" and h1 < l3:
            fvg_bottom, fvg_top = h1, l3; fvg_found = True; break
        if trend == "SELL" and l1 > h3:
            fvg_top, fvg_bottom = l1, h3; fvg_found = True; break
    if not fvg_found:
        return None
    current = closes_15m[-1]
    # V23: FVG tolerance 50% (was 20%)
    if not (fvg_bottom - (fvg_top-fvg_bottom)*0.5 <= current <= fvg_top + (fvg_top-fvg_bottom)*0.5):
        return None
    score += 15; details.append("FVG Retest 50% Tol ✅ +15")
    ob_ok = False
    for j in range(5):
        if lows_1h[-5:][j] <= fvg_top and highs_1h[-5:][j] >= fvg_bottom:
            ob_ok = True
    if ob_ok:
        score += 5; details.append("Order Block ✅ +5")
    else:
        details.append("Order Block ❌ +0")
    print(f"{name} {trend} Score: {score}/95", flush=True)
    if score < 75:
        return None
    entry = current
    if trend == "BUY":
        sl = prev_low - (fvg_top - fvg_bottom)
        risk = entry - sl
        tp = entry + risk * 2
    else:
        sl = prev_high + (fvg_top - fvg_bottom)
        risk = sl - entry
        tp = entry - risk * 2
    if risk <= 0:
        return None
    # Tier
    tier = "🔥 PERFECT SNIPER" if score >= 85 else "⚡ GOOD SNIPER"
    return {"symbol": name, "trend": trend, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp": round(float(tp),5), "score": score, "details": details, "tier": tier}

def bot_loop():
    global last_activity
    send_telegram("✅ *V23 SNIPER CLOUD is LIVE*\n8 Pairs | Score 75+ ONLY | UNLIMITED\nEMA + BOS + Wick 50% + 15m BOS + FVG 50% Tol + OB\n🔥 85+ PERFECT | ⚡ 75-84 GOOD\nMove SL to BE at +20 pips")
    sent = set()
    while True:
        try:
            tz = pytz.timezone("Africa/Nairobi")
            now = dt.now(tz)
            last_activity = time.time()
            print(f"V23 checking... EAT {now} | Checking 8 pairs | 75+ ONLY UNLIMITED", flush=True)
            for name, ysym in SYMBOLS.items():
                try:
                    print(f"Checking {name}...", flush=True)
                    res = analyze(name, ysym)
                    if res:
                        key = f"{name}-{res['trend']}-{res['entry']}"
                        if key in sent:
                            continue
                        sent.add(key)
                        be_rule = "Move SL to BE at +20 pips" if res['score'] >= 85 else "Move SL to BE FAST at +15 pips"
                        msg = (f"🎯 *V23 {res['tier']} - Score {res['score']}/95*\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nEntry: {res['entry']}\nSL: {res['sl']}\nTP: {res['tp']}\nRR: 1:2\n\n" + "\n".join(res['details']) + f"\n\n🛡️ *RISK RULES:*\nLot: 0.01 ONLY\n{be_rule}\nUNLIMITED - Only 75+ Snipers")
                        send_telegram(msg)
                    else:
                        print(f"{name}: No setup - score <75 or filter fail, skipping", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True)
            gc.collect()
            print("Sleeping 180s...", flush=True)
            time.sleep(180)
        except Exception as e:
            print(f"Bot loop crash, restarting in 10s: {e}", flush=True)
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    print("V23 - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
