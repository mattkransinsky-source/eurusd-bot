import os, time, requests, threading, pytz, gc
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def home():
    return "V34 SHARP ALIVE", 200

@app.route('/health', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def health():
    return "OK", 200

@app.route('/<path:path>', methods=['GET', 'HEAD', 'POST', 'OPTIONS'])
def catch_all(path):
    return "V34 SHARP ALIVE", 200

@app.errorhandler(404)
def not_found(e):
    return "V34 SHARP ALIVE", 200

@app.errorhandler(405)
def method_not_allowed(e):
    return "V34 SHARP ALIVE", 200

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
# V34: TIGHTER caps - Gold was killing account
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

def analyze(name, yahoo_sym):
    df_4h = get_data(yahoo_sym, "4h", "10d")
    df_1h = get_data(yahoo_sym, "1h", "5d")
    df_15m = get_data(yahoo_sym, "15m", "2d")
    df_5m = get_data(yahoo_sym, "5m", "2d")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m) < 50 or len(df_15m) < 50 or len(df_1h) < 20 or len(df_4h) < 20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    # V34: Use CLOSED candles only - drop last unfinished candle to avoid false signals
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

    # --- HTF ---
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

    # --- 15m Trend - V34 FIX: Real EMA 200 ---
    ema50 = ema(closes_15m, 50)
    ema200 = ema(closes_15m, 200)
    price = closes_15m[-1]
    trend = None
    if price > ema50 and price > ema200:
        trend = "BUY"; score += 30; details.append("15m EMA 50+200 ✅ +30")
    elif price < ema50 and price < ema200:
        trend = "SELL"; score += 30; details.append("15m EMA 50+200 ✅ +30")
    else:
        return {"fail": True, "score": score, "reason": "EMA fail - ranging / price between EMAs", "trend": "NONE"}

    # V34: 1H MUST agree - no neutral pass
    if htf_trend is not None and htf_trend != trend:
        return {"fail": True, "score": score, "reason": f"1H Conflict - 1H {htf_trend} vs 15m {trend}", "trend": trend, "details": details}
    if htf_trend == trend:
        details.append(f"1H Agrees ✅ {htf_trend}")
    else:
        details.append(f"1H Neutral - allowing 15m {trend}")

    # V34: 4H BLOCKS again - no warning only
    if htf4_trend is not None and htf4_trend != trend:
        return {"fail": True, "score": score, "reason": f"4H Conflict BLOCK - 4H {htf4_trend} vs 15m {trend}", "trend": trend, "details": details}
    if htf4_trend == trend:
        details.append(f"4H Agrees ✅ {htf4_trend}")
    else:
        details.append(f"4H Neutral - allowing 15m {trend}")

    rsi_15m = rsi(closes_15m, 14)
    if trend == "BUY" and rsi_15m > 70:
        return {"fail": True, "score": score, "reason": f"RSI Overbought {rsi_15m:.1f} - skip BUY", "trend": trend, "details": details}
    if trend == "SELL" and rsi_15m < 30:
        return {"fail": True, "score": score, "reason": f"RSI Oversold {rsi_15m:.1f} - skip SELL", "trend": trend, "details": details}
    # V34: Also block chasing into extreme RSI
    if trend == "BUY" and rsi_15m > 60:
        return {"fail": True, "score": score, "reason": f"RSI {rsi_15m:.1f} too high to BUY - already pumped", "trend": trend, "details": details}
    if trend == "SELL" and rsi_15m < 40:
        return {"fail": True, "score": score, "reason": f"RSI {rsi_15m:.1f} too low to SELL - already dumped", "trend": trend, "details": details}
    details.append(f"RSI {rsi_15m:.1f} OK ✅")

    # --- ATR early for filters ---
    atr_val = atr(highs_5m, lows_5m, closes_5m, 14)
    if atr_val <= 0:
        atr_val = abs(price * 0.001)

    # V34 SHARP: ANTI-CHASE + EXHAUSTION - The Opposite Fix
    # If last 3 closed candles moved > 0.8x ATR in signal direction, move is done
    last3_move = closes_5m[-1] - closes_5m[-4]
    if trend == "BUY" and last3_move > atr_val * 0.8:
        return {"fail": True, "score": score, "reason": f"CHASE BLOCK - Already pumped {last3_move:.5f} > 0.8 ATR - wait for pullback", "trend": trend, "details": details}
    if trend == "SELL" and last3_move < -atr_val * 0.8:
        return {"fail": True, "score": score, "reason": f"CHASE BLOCK - Already dumped {last3_move:.5f} < -0.8 ATR - wait for pullback", "trend": trend, "details": details}
    details.append(f"Anti-Chase ✅ Last3 move {last3_move:.5f} within ATR")

    # V34: 3 big candles same direction = exhausted
    big_count = 0
    for i in range(-4, -1):
        rng = highs_5m[i] - lows_5m[i]
        if rng > atr_val * 0.9:
            if trend == "BUY" and closes_5m[i] > closes_5m[i-1]:
                big_count += 1
            if trend == "SELL" and closes_5m[i] < closes_5m[i-1]:
                big_count += 1
    if big_count >= 3:
        return {"fail": True, "score": score, "reason": "EXHAUSTION - 3 big candles in signal direction", "trend": trend, "details": details}

    # --- BOS 15m ---
    high_15m_prev = max(highs_15m[-20:-1])
    low_15m_prev = min(lows_15m[-20:-1])
    last_15m_close = closes_15m[-1]
    bos_ok = (trend == "BUY" and last_15m_close > high_15m_prev) or (trend == "SELL" and last_15m_close < low_15m_prev)
    if bos_ok:
        score += 30; details.append("15m BOS ✅ +30")
    else:
        details.append("15m BOS ❌ +0")

    # --- Wick Sweep 5m ---
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

    # --- BOS 5m ---
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

    # --- FVG - V34: Must be MOST RECENT + price INSIDE + rejection ---
    fvg_found = False
    fvg_top = fvg_bottom = 0
    fvg_ok = False
    # Search backwards for most recent FVG, not first old one
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
    if not fvg_found:
        return {"fail": True, "score": score, "reason": "No FVG - SKIP, no market entry", "trend": trend, "details": details}
    # V34: Price MUST be inside FVG - no 50% tolerance outside
    if not (fvg_bottom <= current <= fvg_top):
        return {"fail": True, "score": score, "reason": f"FVG found but price NOT inside retest - Price {current:.5f} vs FVG {fvg_bottom:.5f}-{fvg_top:.5f} - WAIT", "trend": trend, "details": details}
    # V34: Rejection required - last closed candle must reject from FVG edge
    last_c = df_5m.iloc[-1]
    last_rng = last_c['High'] - last_c['Low']
    rejection_ok = False
    if last_rng > 0:
        if trend == "BUY":
            # Wick from bottom
            wick = last_c['Close'] - last_c['Low']
            if wick / last_rng > 0.4:
                rejection_ok = True
        else:
            wick = last_c['High'] - last_c['Close']
            if wick / last_rng > 0.4:
                rejection_ok = True
    if not rejection_ok:
        return {"fail": True, "score": score, "reason": "No rejection candle in FVG - price just sitting, WAIT", "trend": trend, "details": details}
    score += 25; details.append("FVG Retest INSIDE + Rejection ✅ +25")
    fvg_ok = True

    print(f"{name} {trend} Score: {score}/120 RSI:{rsi_15m:.1f} 1H:{htf_trend} 4H:{htf4_trend}", flush=True)
    if score < 70:
        return {"fail": True, "score": score, "reason": "Score <70", "trend": trend, "details": details}
    # V34 SHARP: Need ALL THREE - BOS + FVG + Sweep for manual A+
    if not (bos_ok and fvg_ok and sweep_ok):
        return {"fail": True, "score": score, "reason": f"Quality Gate FAIL - Need BOS+FVG+Sweep. BOS:{bos_ok} FVG:{fvg_ok} Sweep:{sweep_ok}", "trend": trend, "details": details}

    # V34: Entry = FVG Mid ONLY - NO market chase
    fvg_mid = (fvg_top + fvg_bottom) / 2
    entry = fvg_mid
    entry_mode = "LIMIT - FVG Mid - WAIT for fill"
    # If entry too far from live price, skip - don't chase
    if abs(entry - current) / current > 0.002:
        return {"fail": True, "score": score, "reason": "Entry too far from price - chasing, skip", "trend": trend, "details": details}

    if entry < current and trend == "BUY":
        order_type = "BUY LIMIT"
    elif entry > current and trend == "BUY":
        order_type = "BUY STOP - SKIP, use LIMIT only"
        return {"fail": True, "score": score, "reason": "BUY STOP not allowed in V34 - only LIMIT pullbacks", "trend": trend, "details": details}
    elif entry > current and trend == "SELL":
        order_type = "SELL LIMIT"
    elif entry < current and trend == "SELL":
        order_type = "SELL STOP - SKIP, use LIMIT only"
        return {"fail": True, "score": score, "reason": "SELL STOP not allowed in V34 - only LIMIT pullbacks", "trend": trend, "details": details}
    else:
        order_type = f"{trend} LIMIT"

    # V34: SL = ATR 1.0x ONLY - No widening, tighter
    if trend == "BUY":
        sl = entry - atr_val * 1.0
        risk = entry - sl
        tp = entry + risk * 1.5  # V34: 1.5R
    else:
        sl = entry + atr_val * 1.0
        risk = sl - entry
        tp = entry - risk * 1.5
    if risk <= 0:
        return {"fail": True, "score": score, "reason": "Risk <=0", "trend": trend}
    max_risk = MAX_RISK.get(name, 9999)
    if risk > max_risk:
        return {"fail": True, "score": score, "reason": f"SL Cap - Risk {risk:.5f} > Max {max_risk} - Volatile, skip", "trend": trend, "details": details}
    if risk / entry > 0.01:
        return {"fail": True, "score": score, "reason": "SL too wide", "trend": trend, "details": details}
    confidence = round((score / 120) * 100, 1)
    tier = "🔥 SHARP A+"
    be_price = entry + risk * 0.5 if trend == "BUY" else entry - risk * 0.5
    return {"symbol": name, "trend": trend, "order_type": order_type, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp": round(float(tp),5), "score": score, "confidence": confidence, "details": details, "tier": tier, "live_price": round(float(current),5), "rsi": round(float(rsi_15m),1), "htf": htf_trend, "htf4": htf4_trend, "entry_mode": entry_mode, "be_price": round(float(be_price),5), "risk": round(float(risk),5)}

def bot_loop():
    global last_activity
    send_telegram("✅ *V34 SHARP - MANUAL MODE is LIVE*\n5 Pairs | 15m + 5m + 1H + 4H(BLOCK) | Score 70+ /120 | TP 1.5R\n🔥 NEW SHARP: Anti-Chase | Exhaustion | Rejection Required | BOS+FVG+Sweep ALL | Real EMA200 | LIMIT Only\n🛡️ SL: ATR 1.0x Tight | BE at 0.5R\n🎯 MANUAL: Only take A+ | If not A+, SKIP\n⏰ SESSION: 09-21 EAT | GOLD 11-19 EAT")
    sent = set()
    last_hunt_msg = time.time()
    last_sent_time = {}
    while True:
        try:
            tz = pytz.timezone("Africa/Nairobi")
            now = dt.now(tz)
            last_activity = time.time()
            hour = now.hour + now.minute / 60.0
            in_session = 9.0 <= hour < 21.0
            if not in_session:
                print(f"OUT OF SESSION - EAT {now} - sleeping, no signals", flush=True)
                time.sleep(60)
                continue
            print(f"V34 checking... EAT {now} | SESSION ON 09-21 | SHARP", flush=True)
            found_this_round = 0
            best_score_this_round = 0
            best_pair_this_round = "None"
            for name, ysym in SYMBOLS.items():
                try:
                    if name == "XAUUSD" and not (11.0 <= hour < 19.0):
                        print(f"{name}: Skipped - Gold only 11-19 EAT", flush=True)
                        continue
                    print(f"Checking {name}...", flush=True)
                    res = analyze(name, ysym)
                    if res and not res.get("fail"):
                        found_this_round += 1
                        last_hunt_msg = time.time()
                        cool_key = f"{name}-{res['trend']}"
                        if cool_key in last_sent_time and (time.time() - last_sent_time[cool_key]) < 3600:
                            print(f"{name} {res['trend']} in cooldown 60min, skipping", flush=True)
                            continue
                        key = f"{name}-{res['trend']}-{res['entry']}"
                        if key in sent:
                            continue
                        sent.add(key)
                        last_sent_time[cool_key] = time.time()
                        msg = (f"🔥 *V34 {res['tier']} - Score {res['score']}/120 ({res['confidence']}%) *\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder Type: {res['order_type']}\nEntry Mode: {res['entry_mode']}\nLive Price: {res['live_price']}\nEntry: {res['entry']}\nSL: {res['sl']} (ATR 1.0x Tight)\nTP: {res['tp']} (1.5R - Close 100%)\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\n\n" + "\n".join(res['details']) + f"\n\n🔥 *MANUAL SHARP CHECK:*\nChase Check: ✅ PASS - Not chased\nRejection: ✅ YES - Wick in FVG\nTake it? ✅ YES - A+ ONLY\n\n🛡️ *MANUAL RULES:*\nLot: 0.01 ONLY\n✅ BREAKEVEN: When price hits {res['be_price']}, MOVE SL TO ENTRY!\n⏰ MAX HOLD 1H - Close after 1h if not at TP\n⏰ If LIMIT not filled in 15 mins, CANCEL it\nSession: 09-21 EAT | Gold 11-19 EAT")
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
                send_telegram(f"👀 *Still Hunting - SHARP Mode...*\n\nNo A+ setup yet. Waiting for BOS+FVG+Sweep + No Chase.\nBest close: {best_pair_this_round} - {best_score_this_round}/120\nEAT {now.strftime('%H:%M')} | Session 09-21 EAT")
                last_hunt_msg = time.time()
            # Clear sent cache every 4h to allow fresh signals
            if len(sent) > 200:
                sent.clear()
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
    print("V34 SHARP MANUAL - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
