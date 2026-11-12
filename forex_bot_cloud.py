import os, time, requests, threading, pytz, gc
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def home():
    return "V30 SCALPER ALIVE", 200

@app.route('/health', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def health():
    return "OK", 200

@app.route('/<path:path>', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def catch_all(path):
    return "V30 SCALPER ALIVE", 200

@app.errorhandler(404)
def not_found(e):
    return "V30 SCALPER ALIVE", 200

@app.errorhandler(405)
def method_not_allowed(e):
    return "V30 SCALPER ALIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask on port {port}...", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# V30 SCALPER - 5 FAST PAIRS ONLY, 15m + 5m, 75/125, QUICK TPs
SYMBOLS = {
    "XAUUSD": "GC=F",
    "GBPJPY": "GBPJPY=X",
    "US30": "YM=F",
    "GBPUSD": "GBPUSD=X",
    "EURUSD": "EURUSD=X",
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

def get_data(yahoo_symbol, interval, period="2d"):
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
    # SCALPER: 15m = trend, 5m = entry
    df_15m = get_data(yahoo_sym, "15m", "2d")
    df_5m = get_data(yahoo_sym, "5m", "2d")
    if df_15m is None or df_5m is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m) < 50 or len(df_15m) < 50:
        return {"fail": True, "score": 0, "reason": f"Not enough candles 15m={len(df_15m)} 5m={len(df_5m)}"}
    closes_15m = df_15m['Close'].tolist()
    closes_5m = df_5m['Close'].tolist()
    highs_5m = df_5m['High'].tolist()
    lows_5m = df_5m['Low'].tolist()
    highs_15m = df_15m['High'].tolist()
    lows_15m = df_15m['Low'].tolist()
    score = 0
    details = []
    ema50 = ema(closes_15m, 50)
    ema200 = ema(closes_15m, 20)
    price = closes_15m[-1]
    trend = None
    if price > ema50 and price > ema200:
        trend = "BUY"; score += 30; details.append("15m EMA 50+200 ✅ +30")
    elif price < ema50 and price < ema200:
        trend = "SELL"; score += 30; details.append("15m EMA 50+200 ✅ +30")
    else:
        return {"fail": True, "score": score, "reason": "EMA fail - ranging", "trend": "NONE"}
    high_15m_prev = max(highs_15m[-20:-1])
    low_15m_prev = min(lows_15m[-20:-1])
    last_15m_close = closes_15m[-1]
    bos_ok = (trend == "BUY" and last_15m_close > high_15m_prev) or (trend == "SELL" and last_15m_close < low_15m_prev)
    if bos_ok:
        score += 30; details.append("15m BOS ✅ +30")
    else:
        details.append("15m BOS ❌ +0")
    prev_low = min(lows_5m[-20:-2])
    prev_high = max(highs_5m[-20:-2])
    sweep_ok = False
    for idx in [-6, -5, -4, -3, -2]:
        sweep_c = df_5m.iloc[idx]
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
    if sweep_ok:
        score += 20; details.append("5m Wick Sweep 50%+ ✅ +20")
    else:
        details.append("Wick Sweep ❌ +0")
    bos5_ok = False
    for i in range(len(df_5m)-5, len(df_5m)-1):
        c = closes_5m[i]
        if trend == "BUY" and c > max(highs_5m[i-5:i]):
            bos5_ok = True
        if trend == "SELL" and c < min(lows_5m[i-5:i]):
            bos5_ok = True
    if bos5_ok:
        score += 15; details.append("5m BOS ✅ +15")
    else:
        details.append("5m BOS ❌ +0")
    fvg_found = False
    fvg_top = fvg_bottom = 0
    fvg_ok = False
    for i in range(len(df_5m)-15, len(df_5m)-2):
        h1 = highs_5m[i]; l1 = lows_5m[i]
        h3 = highs_5m[i+2]; l3 = lows_5m[i+2]
        if trend == "BUY" and h1 < l3:
            fvg_bottom, fvg_top = h1, l3; fvg_found = True; break
        if trend == "SELL" and l1 > h3:
            fvg_top, fvg_bottom = l1, h3; fvg_found = True; break
    current = closes_5m[-1]
    if not fvg_found:
        fvg_top = current * 1.001
        fvg_bottom = current * 0.999
        details.append("FVG ❌ +0 - using price as entry")
    elif not (fvg_bottom - (fvg_top-fvg_bottom)*0.5 <= current <= fvg_top + (fvg_top-fvg_bottom)*0.5):
        details.append("FVG found but price not in retest ❌ +0")
    else:
        score += 25; details.append("FVG Retest 50% Tol ✅ +25")
        fvg_ok = True
    ob_ok = False
    for j in range(5):
        if lows_15m[-5:][j] <= fvg_top and highs_15m[-5:][j] >= fvg_bottom:
            ob_ok = True
    if ob_ok:
        score += 5; details.append("Order Block ✅ +5")
    else:
        details.append("Order Block ❌ +0")
    print(f"{name} {trend} Score: {score}/125", flush=True)
    if score < 75:
        return {"fail": True, "score": score, "reason": "Score <75", "trend": trend, "details": details}
    if not bos_ok and not fvg_ok:
        return {"fail": True, "score": score, "reason": "Quality Gate FAIL - No BOS + No FVG", "trend": trend, "details": details}
    if fvg_found:
        entry = (fvg_top + fvg_bottom) / 2
    else:
        entry = current
    if abs(entry - current) / current > 0.008:
        return {"fail": True, "score": score, "reason": "Entry too far - chasing", "trend": trend, "details": details}
    if trend == "BUY":
        if abs(entry - current) < (current * 0.0002):
            order_type = "BUY MARKET - Buy Now"
        elif entry < current:
            order_type = "BUY LIMIT"
        else:
            order_type = "BUY STOP"
    else:
        if abs(entry - current) < (current * 0.0002):
            order_type = "SELL MARKET - Sell Now"
        elif entry > current:
            order_type = "SELL LIMIT"
        else:
            order_type = "SELL STOP"
    if trend == "BUY":
        sl = prev_low - (fvg_top - fvg_bottom)
        risk = entry - sl
        # SCALPER TPs - Small & Fast
        tp1 = entry + risk * 0.5
        tp2 = entry + risk * 1.0
        tp3 = entry + risk * 1.5
    else:
        sl = prev_high + (fvg_top - fvg_bottom)
        risk = sl - entry
        tp1 = entry - risk * 0.5
        tp2 = entry - risk * 1.0
        tp3 = entry - risk * 1.5
    if risk <= 0:
        return {"fail": True, "score": score, "reason": "Risk <=0", "trend": trend}
    if risk / entry > 0.015:
        return {"fail": True, "score": score, "reason": "SL too wide", "trend": trend, "details": details}
    confidence = round((score / 125) * 100, 1)
    tier = "⚡ SCALPER 75+"
    return {"symbol": name, "trend": trend, "order_type": order_type, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp1": round(float(tp1),5), "tp2": round(float(tp2),5), "tp3": round(float(tp3),5), "score": score, "confidence": confidence, "details": details, "tier": tier, "live_price": round(float(current),5)}

def bot_loop():
    global last_activity
    send_telegram("✅ *V30 SCALPER is LIVE*\n5 Fast Pairs | 15m + 5m | Score 75+ /125 | TP 0.5R / 1R / 1.5R\nSession: 15:00-19:00 EAT ONLY (London+NY Overlap)\n⚡ MAX HOLD: 1 HOUR - Scalps Only\nTP1 0.5R 50% | TP2 1R 30% | TP3 1.5R 20%\n👀 Hunting every 1 min")
    sent = set()
    last_hunt_msg = time.time()
    while True:
        try:
            tz = pytz.timezone("Africa/Nairobi")
            now = dt.now(tz)
            last_activity = time.time()
            hour = now.hour
            # SCALPER SESSION - Overlap only for max speed
            in_session = 15 <= hour < 19
            print(f"V30 SCALPER checking... EAT {now} | Session: {'ON' if in_session else 'OFF'} | 75+ /125", flush=True)
            if not in_session:
                if (time.time() - last_hunt_msg) >= 3600:
                    send_telegram(f"😴 *Outside Scalp Session*\nScalper sleeps. Back at 15:00 EAT (London+NY Overlap).\nTime now: {now.strftime('%H:%M')} EAT")
                    last_hunt_msg = time.time()
                time.sleep(60)
                continue
            found_this_round = 0
            best_score_this_round = 0
            best_pair_this_round = "None"
            for name, ysym in SYMBOLS.items():
                try:
                    print(f"Checking {name}...", flush=True)
                    res = analyze(name, ysym)
                    if res and not res.get("fail"):
                        found_this_round += 1
                        last_hunt_msg = time.time()
                        key = f"{name}-{res['trend']}-{res['entry']}"
                        if key in sent:
                            continue
                        sent.add(key)
                        msg = (f"⚡ *V30 {res['tier']} - Score {res['score']}/125 ({res['confidence']}%) *\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder Type: {res['order_type']}\nLive Price: {res['live_price']}\nEntry: {res['entry']}\nSL: {res['sl']}\nTP1: {res['tp1']} (0.5R - Close 50%)\nTP2: {res['tp2']} (1R - Close 30%)\nTP3: {res['tp3']} (1.5R - Close 20% Runner)\n\n" + "\n".join(res['details']) + f"\n\n🛡️ *SCALP RULES:*\nLot: 0.01 ONLY\nMove SL to BE when TP1 hits\n⏰ MAX HOLD 1H - Close after 1h if not at TP1\n⏰ If LIMIT not filled in 30 mins, CANCEL it\nSession: 15:00-19:00 EAT ONLY")
                        send_telegram(msg)
                    else:
                        sc = res.get("score", 0) if res else 0
                        if sc > best_score_this_round:
                            best_score_this_round = sc
                            best_pair_this_round = name
                        reason = res.get("reason", "Unknown") if res else "None"
                        tr = res.get("trend", "?") if res else "?"
                        print(f"{name}: Score {sc}/125 - {tr} - {reason} - skipping", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True)
            if found_this_round == 0 and (time.time() - last_hunt_msg) >= 1800:
                send_telegram(f"👀 *Still Hunting Scalps...*\n\nNo 75+ scalp yet. Checking 5 fast pairs.\nBest close: {best_pair_this_round} - {best_score_this_round}/125\nEAT {now.strftime('%H:%M')}")
                last_hunt_msg = time.time()
            gc.collect()
            print("Sleeping 60s...", flush=True)
            time.sleep(60)
        except Exception as e:
            print(f"Bot loop crash, restarting in 10s: {e}", flush=True)
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    print("V30 SCALPER - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
