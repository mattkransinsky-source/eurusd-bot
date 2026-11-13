import os, time, requests, threading, pytz, gc
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def home():
    return "V34.4 SILENT-FIX ALIVE", 200

@app.route('/health', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def health():
    return "OK", 200

@app.route('/<path:path>', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def catch_all(path):
    return "V34.4 SILENT-FIX ALIVE", 200

@app.errorhandler(404)
def not_found(e):
    return "V34.4 SILENT-FIX ALIVE", 200

@app.errorhandler(405)
def method_not_allowed(e):
    return "V34.4 SILENT-FIX ALIVE", 200

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
MAX_RISK = {
    "XAUUSD": 5.0,
    "GBPJPY": 0.8,
    "US30": 80.0,
    "GBPUSD": 0.005,
    "EURUSD": 0.005,
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

def find_order_blocks(df_5m, trend):
    highs = df_5m['High'].tolist()
    lows = df_5m['Low'].tolist()
    closes = df_5m['Close'].tolist()
    opens = df_5m['Open'].tolist()
    obs = []
    n = len(df_5m)
    for i in range(max(5, n-30), n-3):
        if closes[i] > max(highs[i-5:i]):
            for j in range(i-1, max(i-5, 0), -1):
                if closes[j] < opens[j]:
                    top, bottom = highs[j], lows[j]
                    broken = False
                    for k in range(j+1, n):
                        if closes[k] < bottom:
                            broken = True
                            break
                    if broken and trend == "SELL":
                        obs.append({"type": "BREAKER_BEAR", "top": top, "bottom": bottom, "mid": (top+bottom)/2, "idx": j, "fresh": (n-j) < 12})
                    elif not broken and trend == "BUY":
                        taps = 0
                        for k in range(j+1, n):
                            if bottom <= lows[k] <= top:
                                taps += 1
                        ob_type = "MITIGATION_BULL" if taps >= 1 else "BULL_OB"
                        obs.append({"type": ob_type, "top": top, "bottom": bottom, "mid": (top+bottom)/2, "idx": j, "fresh": (n-j) < 12, "taps": taps})
                    break
        if closes[i] < min(lows[i-5:i]):
            for j in range(i-1, max(i-5, 0), -1):
                if closes[j] > opens[j]:
                    top, bottom = highs[j], lows[j]
                    broken = False
                    for k in range(j+1, n):
                        if closes[k] > top:
                            broken = True
                            break
                    if broken and trend == "BUY":
                        obs.append({"type": "BREAKER_BULL", "top": top, "bottom": bottom, "mid": (top+bottom)/2, "idx": j, "fresh": (n-j) < 12})
                    elif not broken and trend == "SELL":
                        taps = 0
                        for k in range(j+1, n):
                            if bottom <= highs[k] <= top:
                                taps += 1
                        ob_type = "MITIGATION_BEAR" if taps >= 1 else "BEAR_OB"
                        obs.append({"type": ob_type, "top": top, "bottom": bottom, "mid": (top+bottom)/2, "idx": j, "fresh": (n-j) < 12, "taps": taps})
                    break
    return obs

def analyze(name, yahoo_sym):
    # V34.4 FIX: Enough history for EMA200
    df_4h = get_data(yahoo_sym, "4h", "30d")
    df_1h = get_data(yahoo_sym, "1h", "10d")
    df_15m = get_data(yahoo_sym, "15m", "5d")
    df_5m = get_data(yahoo_sym, "5m", "2d")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m) < 50 or len(df_15m) < 50 or len(df_1h) < 20 or len(df_4h) < 20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    df_5m = df_5m.iloc[:-1]
    df_15m = df_15m.iloc[:-1]

    closes_15m = df_15m['Close'].tolist()
    closes_5m = df_5m['Close'].tolist()
    closes_1h = df_1h['Close'].tolist()
    closes_4h = df_4h['Close'].tolist()
    highs_5m = df_5m['High'].tolist()
    lows_5m = df_5m['Low'].tolist()
    highs_15m = df_15m['High'].tolist()
    lows_15m = df_15m['Low'].tolist()

    score = 0
    details = []
    warnings = []

    ema50_4h = ema(closes_4h, 50)
    ema20_4h = ema(closes_4h, 20)
    price_4h = closes_4h[-1]
    htf4_trend = None
    if price_4h > ema50_4h and price_4h > ema20_4h:
        htf4_trend = "BUY"
    elif price_4h < ema50_4h and price_4h < ema20_4h:
        htf4_trend = "SELL"

    ema50_1h = ema(closes_1h, 50)
    ema20_1h = ema(closes_1h, 20)
    price_1h = closes_1h[-1]
    htf_trend = None
    if price_1h > ema50_1h and price_1h > ema20_1h:
        htf_trend = "BUY"
    elif price_1h < ema50_1h and price_1h < ema20_1h:
        htf_trend = "SELL"

    # V34.4: EMA200 confirmation, not hard block if data short
    ema50 = ema(closes_15m, 50)
    ema200 = ema(closes_15m, 200)
    price = closes_15m[-1]
    trend = None
    ema200_ok = len(closes_15m) >= 210
    if price > ema50 and (price > ema200 or not ema200_ok):
        trend = "BUY"; score += 30; details.append("15m EMA 50+200 ✅ +30" if ema200_ok else "15m EMA 50 ✅ +30 (EMA200 short data)")
    elif price < ema50 and (price < ema200 or not ema200_ok):
        trend = "SELL"; score += 30; details.append("15m EMA 50+200 ✅ +30" if ema200_ok else "15m EMA 50 ✅ +30 (EMA200 short data)")
    else:
        return {"fail": True, "score": score, "reason": f"EMA fail - price {price:.5f} vs EMA50 {ema50:.5f} EMA200 {ema200:.5f}", "trend": "NONE"}

    htf_conflict = False
    if htf_trend is not None and htf_trend != trend:
        htf_conflict = True
        warnings.append(f"1H Conflict {htf_trend}")
        details.append(f"⚠️ 1H Conflict {htf_trend} vs {trend} - Grade down")
    elif htf_trend == trend:
        details.append(f"1H Agrees ✅ {htf_trend}")
    else:
        details.append(f"1H Neutral - allowing 15m {trend}")

    htf4_conflict = False
    if htf4_trend is not None and htf4_trend != trend:
        htf4_conflict = True
        warnings.append(f"4H Conflict {htf4_trend}")
        details.append(f"⚠️ 4H Conflict {htf4_trend} vs {trend} - Grade down")
    elif htf4_trend == trend:
        details.append(f"4H Agrees ✅ {htf4_trend}")
    else:
        details.append(f"4H Neutral - allowing 15m {trend}")

    rsi_15m = rsi(closes_15m, 14)
    if trend == "BUY" and rsi_15m > 75:
        return {"fail": True, "score": score, "reason": f"RSI Extreme {rsi_15m:.1f}", "trend": trend, "details": details}
    if trend == "SELL" and rsi_15m < 25:
        return {"fail": True, "score": score, "reason": f"RSI Extreme {rsi_15m:.1f}", "trend": trend, "details": details}
    if trend == "BUY" and rsi_15m > 60:
        warnings.append(f"RSI {rsi_15m:.1f} high")
    if trend == "SELL" and rsi_15m < 40:
        warnings.append(f"RSI {rsi_15m:.1f} low")
    details.append(f"RSI {rsi_15m:.1f} ✅")

    atr_val = atr(highs_5m, lows_5m, closes_5m, 14)
    if atr_val <= 0:
        atr_val = abs(price * 0.001)

    last3_move = closes_5m[-1] - closes_5m[-4]
    chase = False
    if trend == "BUY" and last3_move > atr_val * 0.8:
        chase = True
        warnings.append("CHASE - pumped")
        details.append(f"⚠️ CHASE - Pumped {last3_move:.5f} > 0.8 ATR - Use LIMIT")
    elif trend == "SELL" and last3_move < -atr_val * 0.8:
        chase = True
        warnings.append("CHASE - dumped")
        details.append(f"⚠️ CHASE - Dumped {last3_move:.5f} - Use LIMIT")
    else:
        details.append(f"Anti-Chase ✅ Last3 {last3_move:.5f}")

    exhausted = False
    big_count = 0
    for i in range(-4, -1):
        rng = highs_5m[i] - lows_5m[i]
        if rng > atr_val * 0.9:
            if trend == "BUY" and closes_5m[i] > closes_5m[i-1]:
                big_count += 1
            if trend == "SELL" and closes_5m[i] < closes_5m[i-1]:
                big_count += 1
    if big_count >= 3:
        exhausted = True
        warnings.append("EXHAUSTED")
        details.append("⚠️ EXHAUSTION - 3 big candles")

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
        score += 20; details.append("5m Wick Sweep ✅ +20")
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
    fvg_inside = False
    for i in range(len(df_5m)-4, len(df_5m)-16, -1):
        if i < 2:
            break
        h1 = highs_5m[i]; l1 = lows_5m[i]
        h3 = highs_5m[i+2]; l3 = lows_5m[i+2]
        if trend == "BUY" and h1 < l3:
            fvg_bottom, fvg_top = h1, l3; fvg_found = True; break
        if trend == "SELL" and l1 > h3:
            fvg_top, fvg_bottom = l1, h3; fvg_found = True; break
    current = closes_5m[-1]
    if fvg_found:
        if fvg_bottom <= current <= fvg_top:
            fvg_inside = True
            score += 25; details.append("FVG INSIDE ✅ +25")
            fvg_ok = True
        elif abs(current - ((fvg_top+fvg_bottom)/2)) / current <= 0.003:
            score += 15; details.append("FVG Near ✅ +15")
            fvg_ok = True
        else:
            details.append("FVG Far - LIMIT and WAIT")
    else:
        details.append("FVG ❌ +0 - No fresh FVG")

    obs = find_order_blocks(df_5m, trend)
    ob_ok = False
    ob_type = "None"
    ob_top = ob_bottom = ob_mid = 0
    ob_fresh = False
    ob_inside = False
    ob_quality = "Weak"
    if obs:
        best_ob = None
        best_dist = 99999
        for o in obs:
            dist = abs(o["mid"] - current)
            if trend == "BUY" and o["mid"] > current + atr_val * 0.5:
                continue
            if trend == "SELL" and o["mid"] < current - atr_val * 0.5:
                continue
            if dist < best_dist:
                best_dist = dist
                best_ob = o
        if best_ob:
            ob_ok = True
            ob_type = best_ob["type"]
            ob_top, ob_bottom, ob_mid = best_ob["top"], best_ob["bottom"], best_ob["mid"]
            ob_fresh = best_ob.get("fresh", False)
            ob_inside = ob_bottom <= current <= ob_top
            if "BREAKER" in ob_type:
                score += 30; ob_quality = "STRONG - Breaker"
                details.append(f"🔥 BREAKER {ob_type} ✅ +30")
            elif "MITIGATION" in ob_type:
                score += 28; ob_quality = "STRONG - Mitigation 2nd tap"
                details.append(f"🔥 MITIGATION {ob_type} ✅ +28")
            elif ob_fresh:
                score += 25; ob_quality = "FRESH OB"
                details.append(f"OB {ob_type} FRESH ✅ +25 - {ob_bottom:.5f}-{ob_top:.5f}")
            else:
                score += 15; ob_quality = "OLD OB"
                details.append(f"OB {ob_type} OLD ✅ +15")
            if ob_inside:
                score += 10; details.append("OB Price INSIDE ✅ +10")
    else:
        details.append("OB ❌ +0 - No OB found")

    last_c = df_5m.iloc[-1]
    last_rng = last_c['High'] - last_c['Low']
    rejection_ok = False
    if last_rng > 0:
        if trend == "BUY":
            wick = last_c['Close'] - last_c['Low']
            if wick / last_rng > 0.4:
                rejection_ok = True
        else:
            wick = last_c['High'] - last_c['Close']
            if wick / last_rng > 0.4:
                rejection_ok = True
    if rejection_ok:
        score += 10; details.append("Rejection Wick ✅ +10")
    else:
        details.append("Rejection ❌ +0")

    print(f"{name} {trend} Score: {score}/180 RSI:{rsi_15m:.1f} OB:{ob_type} FVG:{fvg_ok}", flush=True)
    # V34.4: Lower threshold to restore volume
    if score < 50:
        return {"fail": True, "score": score, "reason": "Score <50", "trend": trend, "details": details}

    # V34.4: Relaxed tiers - OB OR FVG is enough to send
    confluence = sum([1 for x in [bos_ok, sweep_ok, fvg_ok, ob_ok] if x])
    if confluence >= 3 and not chase and not exhausted and (fvg_inside or ob_inside) and rejection_ok:
        tier = "🔥 A+ SHARP OB"
        tier_label = "A+"
        take_it = "✅ YES - Take it"
    elif confluence >= 2:
        tier = "✅ A - GOOD OB"
        tier_label = "A"
        take_it = "✅ YES - Take it, 0.01 only"
    elif confluence >= 1 and (fvg_ok or ob_ok):
        tier = "⚠️ A- WATCH"
        tier_label = "A-"
        take_it = "⚠️ WAIT - Use LIMIT only, don't market"
    else:
        tier = "👀 WATCH ONLY"
        tier_label = "WATCH"
        take_it = "❌ NO - Watch only, wait for pullback"

    entry = None
    entry_type = "FVG"
    if ob_ok and fvg_found:
        dist_ob = abs(ob_mid - current)
        dist_fvg = abs(((fvg_top+fvg_bottom)/2) - current)
        if ob_inside or dist_ob < dist_fvg:
            entry = ob_mid; entry_type = f"OB {ob_type}"
        else:
            entry = (fvg_top+fvg_bottom)/2; entry_type = "FVG"
    elif ob_ok:
        entry = ob_mid; entry_type = f"OB {ob_type}"
    elif fvg_found:
        entry = (fvg_top+fvg_bottom)/2; entry_type = "FVG"
    else:
        entry = current; entry_type = "Pullback"

    entry_mode = f"LIMIT - {entry_type} Mid - WAIT, don't chase"
    if trend == "BUY":
        order_type = "BUY LIMIT"
    else:
        order_type = "SELL LIMIT"

    if trend == "BUY":
        inval = ob_bottom if ob_ok else (fvg_bottom if fvg_found else entry - atr_val)
        sl_inval = inval - atr_val * 0.2
        sl_atr = entry - atr_val * 1.0
        sl = max(sl_inval, sl_atr)
        risk = entry - sl
        if risk < atr_val * 0.5:
            sl = entry - atr_val * 0.8
            risk = entry - sl
    else:
        inval = ob_top if ob_ok else (fvg_top if fvg_found else entry + atr_val)
        sl_inval = inval + atr_val * 0.2
        sl_atr = entry + atr_val * 1.0
        sl = min(sl_inval, sl_atr)
        risk = sl - entry
        if risk < atr_val * 0.5:
            sl = entry + atr_val * 0.8
            risk = sl - entry

    if risk <= 0:
        return {"fail": True, "score": score, "reason": "Risk <=0", "trend": trend}
    max_risk = MAX_RISK.get(name, 9999)
    if risk > max_risk:
        return {"fail": True, "score": score, "reason": f"SL Cap - Risk {risk:.5f} > Max {max_risk}", "trend": trend, "details": details}
    if risk / entry > 0.01:
        return {"fail": True, "score": score, "reason": "SL too wide", "trend": trend, "details": details}

    if trend == "BUY":
        tp1 = entry + risk * 1.0
        tp2 = entry + risk * 2.0
    else:
        tp1 = entry - risk * 1.0
        tp2 = entry - risk * 2.0

    confidence = round((score / 180) * 100, 1)
    be_price = entry + risk * 0.5 if trend == "BUY" else entry - risk * 0.5
    inval_price = round(float(sl),5)
    return {"symbol": name, "trend": trend, "order_type": order_type, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp": round(float(tp2),5), "tp1": round(float(tp1),5), "tp2": round(float(tp2),5), "score": score, "confidence": confidence, "details": details, "tier": tier, "tier_label": tier_label, "take_it": take_it, "live_price": round(float(current),5), "rsi": round(float(rsi_15m),1), "htf": htf_trend, "htf4": htf4_trend, "entry_mode": entry_mode, "be_price": round(float(be_price),5), "risk": round(float(risk),5), "warnings": warnings, "chase": chase, "fvg_inside": fvg_inside, "rejection": rejection_ok, "ob_type": ob_type, "ob_quality": ob_quality, "entry_type": entry_type, "ob_top": round(float(ob_top),5) if ob_ok else 0, "ob_bottom": round(float(ob_bottom),5) if ob_ok else 0, "inval": inval_price}

def bot_loop():
    global last_activity
    send_telegram("✅ *V34.4 SILENT-FIX LIVE*\n5 Pairs | Score 50+ /180 | Dual TP 1R + 2R\n🔥 OBs: Bull | Bear | Breaker | Mitigation | FVG\n🎯 TIERS: A+ = Take | A = Take 0.01 | A- = LIMIT only | WATCH = Don't take\n🛡️ SL = OB Invalidation | BE at 0.5R\n⏰ SESSION: 09-21 EAT | GOLD 11-19 EAT")
    sent = set()
    last_hunt_msg = time.time()
    last_sent_time = {}
    last_debug_msg = time.time()
   
