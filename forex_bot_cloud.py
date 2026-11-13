import os, time, requests, threading, pytz, gc, traceback
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "V34.6 CLEAN ALIVE", 200

@app.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"V34.6 Flask on {port}", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOLS = {"XAUUSD": "GC=F", "GBPJPY": "GBPJPY=X", "US30": "YM=F", "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X"}
MAX_RISK = {"XAUUSD": 5.0, "GBPJPY": 0.8, "US30": 80.0, "GBPUSD": 0.005, "EURUSD": 0.005}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram env", flush=True)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"Telegram {r.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error {e}", flush=True)

def heartbeat():
    while True:
        print(f"HEARTBEAT {dt.now(pytz.timezone('Africa/Nairobi'))}", flush=True)
        time.sleep(30)

def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def rsi_calc(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100
    rs = ag / al
    return 100 - (100 / (1 + rs))

def atr_calc(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return abs(closes[-1] * 0.001)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period

def get_data(sym, interval, period):
    try:
        df = yf.download(sym, interval=interval, period=period, progress=False, auto_adjust=False, threads=False, timeout=20)
        if df.empty:
            return None
        if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"yf error {sym} {interval} {e}", flush=True)
        return None

def find_obs(df_5m, trend):
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
                        obs.append({"type": "BREAKER_BEAR", "top": top, "bottom": bottom, "mid": (top+bottom)/2, "fresh": (n-j) < 12})
                    elif not broken and trend == "BUY":
                        taps = 0
                        for k in range(j+1, n):
                            if bottom <= lows[k] <= top:
                                taps += 1
                        t = "MITIGATION_BULL" if taps >= 1 else "BULL_OB"
                        obs.append({"type": t, "top": top, "bottom": bottom, "mid": (top+bottom)/2, "fresh": (n-j) < 12})
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
                        obs.append({"type": "BREAKER_BULL", "top": top, "bottom": bottom, "mid": (top+bottom)/2, "fresh": (n-j) < 12})
                    elif not broken and trend == "SELL":
                        taps = 0
                        for k in range(j+1, n):
                            if bottom <= highs[k] <= top:
                                taps += 1
                        t = "MITIGATION_BEAR" if taps >= 1 else "BEAR_OB"
                        obs.append({"type": t, "top": top, "bottom": bottom, "mid": (top+bottom)/2, "fresh": (n-j) < 12})
                    break
    return obs

def analyze(name, ysym):
    df_4h = get_data(ysym, "4h", "30d")
    df_1h = get_data(ysym, "1h", "10d")
    df_15m = get_data(ysym, "15m", "5d")
    df_5m = get_data(ysym, "5m", "2d")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m) < 50 or len(df_15m) < 50 or len(df_1h) < 20 or len(df_4h) < 20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    df_5m = df_5m.iloc[:-1]
    df_15m = df_15m.iloc[:-1]
    c15 = df_15m['Close'].tolist()
    c5 = df_5m['Close'].tolist()
    c1 = df_1h['Close'].tolist()
    c4 = df_4h['Close'].tolist()
    h5 = df_5m['High'].tolist()
    l5 = df_5m['Low'].tolist()
    h15 = df_15m['High'].tolist()
    l15 = df_15m['Low'].tolist()
    score = 0
    details = []
    warnings = []
    e50_4h = ema(c4, 50)
    e20_4h = ema(c4, 20)
    p4 = c4[-1]
    htf4 = "BUY" if p4 > e50_4h and p4 > e20_4h else ("SELL" if p4 < e50_4h and p4 < e20_4h else None)
    e50_1h = ema(c1, 50)
    e20_1h = ema(c1, 20)
    p1 = c1[-1]
    htf = "BUY" if p1 > e50_1h and p1 > e20_1h else ("SELL" if p1 < e50_1h and p1 < e20_1h else None)
    e50 = ema(c15, 50)
    e200 = ema(c15, 200)
    price = c15[-1]
    trend = None
    if price > e50 and price > e200:
        trend = "BUY"
        score += 30
        details.append("EMA 50+200 BUY +30")
    elif price < e50 and price < e200:
        trend = "SELL"
        score += 30
        details.append("EMA 50+200 SELL +30")
    else:
        return {"fail": True, "score": score, "reason": f"EMA fail {price:.5f} vs {e50:.5f}/{e200:.5f}", "trend": "NONE"}
    if htf is not None and htf != trend:
        warnings.append(f"1H Conflict {htf}")
        details.append(f"1H Conflict {htf}")
    elif htf == trend:
        details.append(f"1H Agrees {htf}")
    if htf4 is not None and htf4 != trend:
        warnings.append(f"4H Conflict {htf4}")
        details.append(f"4H Conflict {htf4}")
    elif htf4 == trend:
        details.append(f"4H Agrees {htf4}")
    r = rsi_calc(c15, 14)
    if trend == "BUY" and r > 75:
        return {"fail": True, "score": score, "reason": f"RSI {r:.1f} extreme", "trend": trend, "details": details}
    if trend == "SELL" and r < 25:
        return {"fail": True, "score": score, "reason": f"RSI {r:.1f} extreme", "trend": trend, "details": details}
    details.append(f"RSI {r:.1f}")
    atr_val = atr_calc(h5, l5, c5, 14)
    if atr_val <= 0:
        atr_val = abs(price * 0.001)
    last3 = c5[-1] - c5[-4]
    chase = False
    if trend == "BUY" and last3 > atr_val * 0.8:
        chase = True
        warnings.append("CHASE")
        details.append("CHASE - Use LIMIT")
    elif trend == "SELL" and last3 < -atr_val * 0.8:
        chase = True
        warnings.append("CHASE")
        details.append("CHASE - Use LIMIT")
    else:
        details.append("Anti-Chase OK")
    big = 0
    for i in range(-4, -1):
        rng = h5[i] - l5[i]
        if rng > atr_val * 0.9:
            if trend == "BUY" and c5[i] > c5[i-1]:
                big += 1
            if trend == "SELL" and c5[i] < c5[i-1]:
                big += 1
    exhausted = big >= 3
    if exhausted:
        warnings.append("EXHAUSTED")
        details.append("EXHAUSTION")
    hi_prev = max(h15[-20:-1])
    lo_prev = min(l15[-20:-1])
    bos_ok = (trend == "BUY" and c15[-1] > hi_prev) or (trend == "SELL" and c15[-1] < lo_prev)
    if bos_ok:
        score += 30
        details.append("15m BOS +30")
    else:
        details.append("BOS 0")
    prev_low = min(l5[-20:-2])
    prev_high = max(h5[-20:-2])
    sweep_ok = False
    for idx in [-6, -5, -4, -3, -2]:
        sc = df_5m.iloc[idx]
        if trend == "BUY" and sc['Low'] < prev_low and sc['Close'] > prev_low:
            rng = sc['High'] - sc['Low']
            if rng > 0 and (sc['Close'] - sc['Low']) / rng > 0.5:
                sweep_ok = True
                break
        if trend == "SELL" and sc['High'] > prev_high and sc['Close'] < prev_high:
            rng = sc['High'] - sc['Low']
            if rng > 0 and (sc['High'] - sc['Close']) / rng > 0.5:
                sweep_ok = True
                break
    if sweep_ok:
        score += 20
        details.append("Sweep +20")
    else:
        details.append("Sweep 0")
    bos5 = False
    for i in range(len(df_5m)-5, len(df_5m)-1):
        if trend == "BUY" and c5[i] > max(h5[i-5:i]):
            bos5 = True
        if trend == "SELL" and c5[i] < min(l5[i-5:i]):
            bos5 = True
    if bos5:
        score += 15
        details.append("5m BOS +15")
    fvg_found = False
    fvg_top = 0
    fvg_bottom = 0
    fvg_ok = False
    fvg_inside = False
    for i in range(len(df_5m)-4, len(df_5m)-16, -1):
        if i < 2:
            break
        h1 = h5[i]
        l1 = l5[i]
        h3 = h5[i+2]
        l3 = l5[i+2]
        if trend == "BUY" and h1 < l3:
            fvg_bottom = h1
            fvg_top = l3
            fvg_found = True
            break
        if trend == "SELL" and l1 > h3:
            fvg_top = l1
            fvg_bottom = h3
            fvg_found = True
            break
    current = c5[-1]
    if fvg_found:
        if fvg_bottom <= current <= fvg_top:
            fvg_inside = True
            score += 25
            details.append("FVG INSIDE +25")
            fvg_ok = True
        elif abs(current - ((fvg_top+fvg_bottom)/2)) / current <= 0.003:
            score += 15
            details.append("FVG Near +15")
            fvg_ok = True
        else:
            details.append("FVG Far")
    else:
        details.append("FVG 0")
    obs = find_obs(df_5m, trend)
    ob_ok = False
    ob_type = "None"
    ob_top = 0
    ob_bottom = 0
    ob_mid = 0
    ob_inside = False
    ob_quality = "Weak"
    if obs:
        best = None
        best_dist = 99999
        for o in obs:
            dist = abs(o["mid"] - current)
            if trend == "BUY" and o["mid"] > current + atr_val * 0.5:
                continue
            if trend == "SELL" and o["mid"] < current - atr_val * 0.5:
                continue
            if dist < best_dist:
                best_dist = dist
                best = o
        if best is not None:
            ob_ok = True
            ob_type = best["type"]
            ob_top = best["top"]
            ob_bottom = best["bottom"]
            ob_mid = best["mid"]
            ob_inside = ob_bottom <= current <= ob_top
            if "BREAKER" in ob_type:
                score += 30
                ob_quality = "STRONG Breaker"
                details.append(f"BREAKER {ob_type} +30")
            elif "MITIGATION" in ob_type:
                score += 28
                ob_quality = "STRONG Mitigation"
                details.append(f"MITIGATION {ob_type} +28")
            elif best.get("fresh", False):
                score += 25
                ob_quality = "FRESH OB"
                details.append(f"OB {ob_type} FRESH +25")
            else:
                score += 15
                ob_quality = "OLD OB"
                details.append(f"OB {ob_type} OLD +15")
            if ob_inside:
                score += 10
                details.append("OB INSIDE +10")
    else:
        details.append("OB 0")
    last_c = df_5m.iloc[-1]
    last_rng = last_c['High'] - last_c['Low']
    rej = False
    if last_rng > 0:
        if trend == "BUY" and (last_c['Close'] - last_c['Low']) / last_rng > 0.4:
            rej = True
        if trend == "SELL" and (last_c['High'] - last_c['Close']) / last_rng > 0.4:
            rej = True
    if rej:
        score += 10
        details.append("Rejection +10")
    print(f"{name} {trend} Score {score}/180 RSI {r:.1f} OB {ob_type} FVG {fvg_ok}", flush=True)
    if score < 50:
        return {"fail": True, "score": score, "reason": "Score <50", "trend": trend, "details": details}
    conf = sum([1 for x in [bos_ok, sweep_ok, fvg_ok, ob_ok] if x])
    if conf >= 3 and not chase and not exhausted and (fvg_inside or ob_inside) and rej:
        tier = "🔥 A+ SHARP OB"
        tier_label = "A+"
        take_it = "YES - Take it"
    elif conf >= 2:
        tier = "✅ A - GOOD OB"
        tier_label = "A"
        take_it = "YES - Take it, 0.01 only"
    elif conf >= 1 and (fvg_ok or ob_ok):
        tier = "⚠️ A- WATCH"
        tier_label = "A-"
        take_it = "WAIT - LIMIT only"
    else:
        tier = "👀 WATCH ONLY"
        tier_label = "WATCH"
        take_it = "NO - Watch only"
    if ob_ok and fvg_found:
        if ob_inside or abs(ob_mid - current) < abs(((fvg_top+fvg_bottom)/2) - current):
            entry = ob_mid
            entry_type = f"OB {ob_type}"
        else:
            entry = (fvg_top+fvg_bottom)/2
            entry_type = "FVG"
    elif ob_ok:
        entry = ob_mid
        entry_type = f"OB {ob_type}"
    elif fvg_found:
        entry = (fvg_top+fvg_bottom)/2
        entry_type = "FVG"
    else:
        entry = current
        entry_type = "Pullback"
    order_type = "BUY LIMIT" if trend == "BUY" else "SELL LIMIT"
    if trend == "BUY":
        inval = ob_bottom if ob_ok else (fvg_bottom if fvg_found else entry - atr_val)
        sl = max(inval - atr_val * 0.2, entry - atr_val * 1.0)
        risk = entry - sl
        if risk < atr_val * 0.5:
            sl = entry - atr_val * 0.8
            risk = entry - sl
    else:
        inval = ob_top if ob_ok else (fvg_top if fvg_found else entry + atr_val)
        sl = min(inval + atr_val * 0.2, entry + atr_val * 1.0)
        risk = sl - entry
        if risk < atr_val * 0.5:
            sl = entry + atr_val * 0.8
            risk = sl - entry
    if risk <= 0:
        return {"fail": True, "score": score, "reason": "Risk <=0", "trend": trend}
    max_r = MAX_RISK.get(name, 9999)
    if risk > max_r:
        return {"fail": True, "score": score, "reason": f"SL Cap {risk:.5f} > {max_r}", "trend": trend, "details": details}
    if risk / entry > 0.01:
        return {"fail": True, "score": score, "reason": "SL too wide", "trend": trend, "details": details}
    if trend == "BUY":
        tp1 = entry + risk * 1.0
        tp2 = entry + risk * 2.0
    else:
        tp1 = entry - risk * 1.0
        tp2 = entry - risk * 2.0
    conf_pct = round((score / 180) * 100, 1)
    return {"symbol": name, "trend": trend, "order_type": order_type, "entry": round(float(entry),5), "sl": round(float(sl),5), "tp1": round(float(tp1),5), "tp2": round(float(tp2),5), "score": score, "confidence": conf_pct, "details": details, "tier": tier, "tier_label": tier_label, "take_it": take_it, "live_price": round(float(current),5), "rsi": round(float(r),1), "htf": htf, "htf4": htf4, "entry_type": entry_type, "ob_type": ob_type, "ob_quality": ob_quality, "ob_top": round(float(ob_top),5) if ob_ok else 0, "ob_bottom": round(float(ob_bottom),5) if ob_ok else 0, "warnings": warnings, "chase": chase, "fvg_inside": fvg_inside, "rejection": rej, "inval": round(float(sl),5)}

def bot_loop():
    send_telegram("✅ *V34.6 CLEAN LIVE*\n5 Pairs | Score 50+ /180 | Dual TP 1R + 2R\nOBs + FVG | No watchdog killer\nSession 09-21 EAT | GOLD 11-19 EAT")
    sent = set()
    last_hunt = time.time()
    last_sent_time = {}
    last_debug = time.time()
    debug_reasons = {}
    while True:
        try:
            tz = pytz.timezone("Africa/Nairobi")
            now = dt.now(tz)
            hour = now.hour + now.minute / 60.0
            if not (9.0 <= hour < 21.0):
                print(f"OUT OF SESSION {now} - alive, sleeping", flush=True)
                time.sleep(60)
                continue
            print(f"V34.6 checking {now}", flush=True)
            found = 0
            best_score = 0
            best_pair = "None"
            for name, ysym in SYMBOLS.items():
                try:
                    if name == "XAUUSD" and not (11.0 <= hour < 19.0):
                        continue
                    print(f"Checking {name}...", flush=True)
                    res = analyze(name, ysym)
                    if res and not res.get("fail"):
                        found += 1
                        last_hunt = time.time()
                        cool = f"{name}-{res['trend']}-{res['tier_label']}"
                        if cool in last_sent_time and (time.time() - last_sent_time[cool]) < 1800:
                            continue
                        key = f"{name}-{res['trend']}-{res['entry']}-{res['tier_label']}"
                        if key in sent:
                            continue
                        sent.add(key)
                        last_sent_time[cool] = time.time()
                        warn_txt = "\n".join([f"⚠️ {w}" for w in res.get('warnings', [])]) if res.get('warnings') else "No warnings"
                        msg = f"{res['tier']} - Score {res['score']}/180 ({res['confidence']}%)\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder: {res['order_type']}\nEntry Type: {res['entry_type']} - {res['ob_quality']}\nLive: {res['live_price']}\nEntry: {res['entry']}\nOB: {res['ob_bottom']} - {res['ob_top']} ({res['ob_type']})\nSL: {res['sl']}\nTP1: {res['tp1']} (1R - Close 50%)\nTP2: {res['tp2']} (2R - Close 50%)\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\n\n" + "\n".join(res['details']) + f"\n\nWARNINGS:\n{warn_txt}\n\nDECISION: {res['take_it']}\n\nLot: 0.01 ONLY\nTP1: Close 50%, MOVE SL TO ENTRY\nTP2: Close 50%\nIf closes past {res['inval']}, exit\nMAX HOLD 1H | LIMIT not filled 15m = CANCEL"
                        send_telegram(msg)
                    else:
                        sc = res.get("score", 0) if res else 0
                        if sc > best_score:
                            best_score = sc
                            best_pair = name
                        debug_reasons[name] = f"{res.get('trend','?')} {sc}/180 - {res.get('reason','Unknown')}"
                        print(f"{name}: {sc}/180 - {res.get('reason')}", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True)
                    traceback.print_exc()
            if (time.time() - last_debug) >= 1800:
                dbg = "\n".join([f"{k}: {v}" for k, v in debug_reasons.items()])
                send_telegram(f"🔍 *V34.6 Debug - Alive*\n\n{dbg}\nBest: {best_pair} {best_score}/180\nEAT {now.strftime('%H:%M')}")
                last_debug = time.time()
                debug_reasons = {}
            if found == 0 and (time.time() - last_hunt) >= 1800:
                send_telegram(f"👀 *Still Hunting - V34.6...*\n\nNo 50+ yet.\nBest: {best_pair} - {best_score}/180\nEAT {now.strftime('%H:%M')}")
                last_hunt = time.time()
            if len(sent) > 200:
                sent.clear()
            gc.collect()
            time.sleep(60)
        except Exception as e:
            print(f"Loop crash, retry 10s: {e}", flush=True)
            traceback.print_exc()
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=heartbeat
