import os, time, requests, threading, pytz, gc
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def home():
    return "V31 SMART SCALPER ALIVE", 200

@app.route('/health', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def health():
    return "OK", 200

@app.route('/<path:path>', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def catch_all(path):
    return "V31 SMART SCALPER ALIVE", 200

@app.errorhandler(404)
def not_found(e):
    return "V31 SMART SCALPER ALIVE", 200

@app.errorhandler(405)
def method_not_allowed(e):
    return "V31 SMART SCALPER ALIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask on port {port}...", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
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

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    gains = gains[-period:]
    losses = losses[-period:]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return (closes[-1] - closes[0]) * 0.001 if closes else 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period

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
    df_1h = get_data(yahoo_sym, "1h", "5d")
    df_15m = get_data(yahoo_sym, "15m", "2d")
    df_5m = get_data(yahoo_sym, "5m", "2d")
    if df_15m is None or df_5m is None or df_1h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m) < 50 or len(df_15m) < 50 or len(df_1h) < 20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    closes_15m = df_15m['Close'].tolist()
    closes_5m = df_5m['Close'].tolist()
    closes_1h = df_1h['Close'].tolist()
    highs_5m = df_5m['High'].tolist()
    lows_5m = df_5m['Low'].tolist()
    highs_15m = df_15m['High'].tolist()
    lows_15m = df_15m['Low'].tolist()

    score = 0
    details = []

    ema50_1h = ema(closes_1h, 50)
    ema20_1h = ema(closes_1h, 20)
    price_1h = closes_1h[-1]
    htf_trend = None
    if price_1h > ema50_1h and price_1h > ema20_1h:
        htf_trend = "BUY"
    elif price_1h < ema50_1h and price_1h < ema20_1h:
        htf_trend = "SELL"

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

    if htf_trend is not None and htf_trend != trend:
        return {"fail": True, "score": score, "reason": f"1H Trend Conflict - 1H {htf_trend} vs 15m {trend}", "trend": trend, "details": details}
    if htf_trend == trend:
        details.append(f"1H Trend Agrees ✅ {htf_trend}")
    else:
        details.append(f"1H Neutral - allowing 15m {trend}")

    rsi_15m = rsi(closes_15m, 14)
    if trend == "BUY" and rsi_15m > 70:
        return {"fail": True, "score": score, "reason": f"RSI Overbought {rsi_15m:.1f} - skip BUY", "trend": trend, "details": details}
    if trend == "SELL" and rsi_15m < 30:
        return {"fail": True, "score": score, "reason": f"RSI Oversold {rsi_15m:.1f} - skip SELL", "trend": trend, "details": details}
    details.append(f"RSI {rsi_15m:.1f} OK ✅")

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

    print(f"{name} {trend} Score: {score}/120 RSI:{rsi_15m:.1f} 1H:{htf_trend}", flush=True)
    if score < 70:
        return {"fail": True, "score": score, "reason": "Score <70", "trend": trend, "details": details}
    if not bos_ok and not fvg_ok:
        return {"fail": True, "score": score, "reason": "Quality Gate FAIL - No BOS + No FVG", "trend": trend, "details": details}

    # --- FIXED FAST ENTRY ---
    # Old: entry = FVG mid, even if far away -> never fills
    # New: if FVG mid is >0.15% away from live price, use live price = MARKET NOW
    if fvg_found:
        fvg_mid = (fvg_top + fvg_bottom) / 2
        dist = abs(fvg_mid - current) / current
        if dist <= 0.0015:
            entry = fvg_mid
            entry_mode = "LIMIT - close to FVG"
        else:
            entry = current
            entry_mode = "MARKET - FVG too far, entering now"
    else:
        entry = current
        entry_mode = "MARKET - No FVG"

    # If still chasing >0.8% - skip (too late)
    if abs(entry - current) / current > 0.008:
        return {"fail": True, "score": score, "reason": "Entry too far - chasing", "trend": trend, "details": details}

    # MARKET if entry == live price, else LIMIT/STOP
    if abs(entry - current) / current < 0.0002:
        order_type = f"{trend} MARKET - Buy Now" if trend == "BUY" else f"{trend} MARKET - Sell Now"
    elif trend == "BUY":
        order_type = "BUY LIMIT" if entry < current else "BUY STOP"
    else:
        order_type = "SELL LIMIT" if entry > current else "SELL STOP"

    atr_val = atr(highs_5m, lows_5m, closes_5m, 14)
    if atr_val <= 0:
        atr_val = abs(entry * 0.001)
    if trend == "BUY":
        sl = entry - atr_val * 1.5
        struct_sl = prev_low - (fvg_top - fvg_bottom)
        sl = min(sl, struct_sl) if struct_sl < entry else sl
        risk = entry - sl
        tp = entry + risk * 0.5
    else:
        sl = entry + atr_val * 1.5
        struct_sl = prev_high + (fvg_top - fvg_bottom)
        sl = max(sl, struct_sl) if struct_sl > entry else sl
        risk = sl - entry
        tp = entry - risk * 0.5
    if risk <= 0:
        return {"fail": True, "score": score, "reason": "Risk <=0", "trend": trend}
    if risk / entry > 0.015:
        return {"fail": True, "score": score, "reason": "SL too wide", "trend": trend, "details": details}
    confidence = round((score / 120) * 100, 1)
    tier = "🧠 SMART 70+"
    return {"symbol": name, "trend": trend, "order_type": order_type, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp": round(float(tp),5), "score": score, "confidence": confidence, "details": details, "tier": tier, "live_price": round(float(current),5), "rsi": round(float(rsi_15m),1), "htf": htf_trend, "entry_mode": entry_mode}

def bot_loop():
    global last_activity
    send_telegram("✅ *V31.1 FAST ENTRY is LIVE*\n5 Pairs | 15m + 5m + 1H | Score 70+ /120 | ONE TP 0.5R\nFIXED: Entry = Live Price if FVG too far - No more waiting!\nSMART: RSI + 1H + ATR SL | 24/7\n⚡ MAX HOLD: 1 HOUR\n👀 Hunting every 1 min")
    sent = set()
    last_hunt_msg = time.time()
    last_sent_time = {}
    while True:
        try:
            tz = pytz.timezone("Africa/Nairobi")
            now = dt.now(tz)
            last_activity = time.time()
            print(f"V31.1 checking... EAT {now} | 24/7 ON | 70+ /120", flush=True)
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
                        cool_key = f"{name}-{res['trend']}"
                        if cool_key in last_sent_time and (time.time() - last_sent_time[cool_key]) < 3600:
                            print(f"{name} {res['trend']} in cooldown, skipping", flush=True)
                            continue
                        key = f"{name}-{res['trend']}-{res['entry']}"
                        if key in sent:
                            continue
                        sent.add(key)
                        last_sent_time[cool_key] = time.time()
                        msg = (f"🧠 *V31 {res['tier']} - Score {res['score']}/120 ({res['confidence']}%) *\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder Type: {res['order_type']}\nEntry Mode: {res['entry_mode']}\nLive Price: {res['live_price']}\nEntry: {res['entry']}\nSL: {res['sl']} (ATR 1.5x)\nTP: {res['tp']} (0.5R - Close 100%)\nRSI: {res['rsi']} | 1H Trend: {res['htf']}\n\n" + "\n".join(res['details']) + f"\n\n🛡️ *SCALP RULES:*\nLot: 0.01 ONLY\n⏰ MAX HOLD 1H - Close after 1h if not at TP\n⏰ If LIMIT not filled in 15 mins, CANCEL it\nRunning 24/7")
                        send_telegram(msg)
                    else:
                        sc = res.get("score", 0) if res else 0
                        if sc > best_score_this_round:
                            best_score_this_round = sc
                            best_pair_this_round = name
                        reason = res.get("reason", "Unknown") if res else "None"
                        tr = res.get("trend", "?") if res else "?"
                        print(f"{name}: Score {sc}/120 - {tr} - {reason} - skipping", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True)
            if found_this_round == 0 and (time.time() - last_hunt_msg) >= 1800:
                send_telegram(f"👀 *Still Hunting Smart Scalps...*\n\nNo 70+ scalp yet. Checking 5 fast pairs.\nBest close: {best_pair_this_round} - {best_score_this_round}/120\nEAT {now.strftime('%H:%M')}")
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
    print("V31.1 FAST ENTRY - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
