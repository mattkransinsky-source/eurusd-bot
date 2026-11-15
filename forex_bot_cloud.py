import os, time, requests, threading, pytz, gc, traceback
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "V35.4 SMART++ ALIVE", 200
@app.route('/health')
def health():
    return "OK", 200
@app.route('/<path:path>')
def catch_all(path):
    return "V35.4 SMART++ ALIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"V35.4 Flask on {port}", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = {
    "XAUUSD": "GC=F", "GBPJPY": "GBPJPY=X", "US30": "YM=F",
    "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X", "EURJPY": "EURJPY=X",
    "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X", "USDCHF": "USDCHF=X", "EURGBP": "EURGBP=X",
}
MAX_RISK = {
    "XAUUSD": 5.0, "GBPJPY": 0.8, "US30": 80.0,
    "GBPUSD": 0.005, "EURUSD": 0.005, "EURJPY": 0.8,
    "USDJPY": 0.8, "AUDUSD": 0.005, "USDCAD": 0.005,
    "NZDUSD": 0.005, "USDCHF": 0.005, "EURGBP": 0.005,
}
PAIR_BONUS = {
    "GBPUSD": 5, "EURUSD": 5, "XAUUSD": 5,
    "GBPJPY": 0, "US30": 0,
    "EURJPY": 0, "USDJPY": 0, "AUDUSD": 0, "USDCAD": 0, "NZDUSD": 0, "USDCHF": 0, "EURGBP": 0,
}

PAIR_COOLDOWN_UNTIL = {}
LAST_SIGNAL_PRICE = {}
PAIR_SIGNAL_COUNT = {}
# SMART++ MEMORY: Tracks hot/cold per pair - LEARNS during day
PAIR_HOT_SCORE = {}  # +1 per signal, used for HOT PAIR bonus
PAIR_LAST_SIGNAL_TIME = {}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram env", flush=True)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
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
    if len(values) < period + 1: return 50
    gains=[]; losses=[]
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0)); losses.append(max(-diff, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    rs = ag / al
    return 100 - (100 / (1 + rs))

def atr_calc(highs, lows, closes, period=14):
    if len(closes) < period + 1: return abs(closes[-1] * 0.001)
    trs=[]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period

def get_data(sym, interval, period):
    try:
        df = yf.download(sym, interval=interval, period=period, progress=False, auto_adjust=False, threads=False, timeout=20)
        if df.empty: return None
        if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"yf error {sym} {interval} {e}", flush=True)
        return None

def find_obs(df_5m, trend):
    highs = df_5m['High'].tolist(); lows = df_5m['Low'].tolist()
    closes = df_5m['Close'].tolist(); opens = df_5m['Open'].tolist()
    obs=[]; n=len(df_5m)
    for i in range(max(5, n-30), n-3):
        if closes[i] > max(highs[i-5:i]):
            for j in range(i-1, max(i-5,0), -1):
                if closes[j] < opens[j]:
                    top, bottom = highs[j], lows[j]
                    broken=False
                    for k in range(j+1, n):
                        if closes[k] < bottom: broken=True; break
                    if broken and trend=="SELL":
                        obs.append({"type":"BREAKER_BEAR","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="BUY":
                        taps=0
                        for k in range(j+1,n):
                            if bottom <= lows[k] <= top: taps+=1
                        t="MITIGATION_BULL" if taps>=1 else "BULL_OB"
                        obs.append({"type":t,"top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    break
        if closes[i] < min(lows[i-5:i]):
            for j in range(i-1, max(i-5,0), -1):
                if closes[j] > opens[j]:
                    top, bottom = highs[j], lows[j]
                    broken=False
                    for k in range(j+1,n):
                        if closes[k] > top: broken=True; break
                    if broken and trend=="BUY":
                        obs.append({"type":"BREAKER_BULL","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="SELL":
                        taps=0
                        for k in range(j+1,n):
                            if bottom <= highs[k] <= top: taps+=1
                        t="MITIGATION_BEAR" if taps>=1 else "BEAR_OB"
                        obs.append({"type":t,"top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    break
    return obs

def is_news_time(now):
    if now.weekday() >= 5: return True
    if 15.0 <= (now.hour + now.minute/60.0) < 16.5: return True
    if now.weekday()==4 and now.day<=7 and 15.0 <= (now.hour + now.minute/60.0) < 17.0: return True
    return False

def get_session_label(now):
    hour = now.hour + now.minute/60.0
    if 9.0 <= hour < 12.0: return "LONDON PRIME", True
    if 15.0 <= hour < 19.0: return "NY PRIME", True
    if 12.0 <= hour < 15.0: return "CHOP TIME", False
    if 19.0 <= hour < 21.0: return "LATE - Careful", False
    return "OFF", False

def analyze(name, ysym, now):
    df_4h = get_data(ysym, "4h", "30d")
    df_1h = get_data(ysym, "1h", "10d")
    df_15m = get_data(ysym, "15m", "5d")
    df_5m = get_data(ysym, "5m", "2d")
    df_1m = get_data(ysym, "1m", "1d")
    df_1d = get_data(ysym, "1d", "5d")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m)<50 or len(df_15m)<50 or len(df_1h)<20 or len(df_4h)<20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    df_5m = df_5m.iloc[:-1]; df_15m = df_15m.iloc[:-1]
    if df_1m is not None and len(df_1m)>5:
        df_1m = df_1m.iloc[:-1]
    c15=df_15m['Close'].tolist(); c5=df_5m['Close'].tolist(); c1=df_1h['Close'].tolist(); c4=df_4h['Close'].tolist()
    h5=df_5m['High'].tolist(); l5=df_5m['Low'].tolist(); h15=df_15m['High'].tolist(); l15=df_15m['Low'].tolist()
    score=0; details=[]; warnings=[]; smart_notes=[]; tags=[]
    # --- SMART++ 1: TREND STRENGTH METER - BONUS ONLY, NEVER PENALTY ---
    trend_strength = 50  # 0-100
    trend_label = "NORMAL"
    e50_4h=ema(c4,50); e20_4h=ema(c4,20); p4=c4[-1]
    htf4="BUY" if p4>e50_4h and p4>e20_4h else ("SELL" if p4<e50_4h and p4<e20_4h else None)
    e50_1h=ema(c1,50); e20_1h=ema(c1,20); p1=c1[-1]
    htf="BUY" if p1>e50_1h and p1>e20_1h else ("SELL" if p1<e50_1h and p1<e20_1h else None)
    e50=ema(c15,50); e200=ema(c15,200); price=c15[-1]; trend=None
    if price>e50 and price>e200:
        trend="BUY"; score+=20; details.append("EMA 50+200 BUY +20")
    elif price<e50 and price<e200:
        trend="SELL"; score+=20; details.append("EMA 50+200 SELL +20")
    else:
        return {"fail":True,"score":score,"reason":f"EMA fail {price:.5f} vs {e50:.5f}/{e200:.5f}","trend":"NONE"}
    # TREND STRENGTH: Distance from EMA + Slope - BONUS ONLY
    try:
        dist_pct = abs(price - e50) / price * 100 if price!=0 else 0
        # EMA slope last 5 candles
        ema_series = []
        tmp = c15[0]
        k = 2/(50+1)
        for v in c15:
            tmp = v*k + tmp*(1-k)
            ema_series.append(tmp)
        slope = (ema_series[-1] - ema_series[-5]) / ema_series[-5] * 100 if len(ema_series)>=6 and ema_series[-5]!=0 else 0
        if trend=="BUY": slope_val = slope
        else: slope_val = -slope
        # Strength 0-100
        trend_strength = min(100, max(0, 50 + dist_pct*800 + slope_val*800))
        if trend_strength >= 75:
            trend_label = "STRONG"
            score+=5; details.append(f"TREND STRONG {trend_strength:.0f}% +5 BONUS"); tags.append(f"TREND STRONG {trend_strength:.0f}%")
            smart_notes.append("Strong Trend")
        elif trend_strength >= 55:
            trend_label = "NORMAL"
            details.append(f"Trend Normal {trend_strength:.0f}%")
            tags.append(f"TREND NORMAL {trend_strength:.0f}%")
        else:
            trend_label = "WEAK"
            # SMART++: WEAK = TAG ONLY, NO PENALTY. Signal still sent.
            details.append(f"Trend Weak {trend_strength:.0f}% - Tag only, NOT block")
            tags.append(f"TREND WEAK {trend_strength:.0f}% - Caution")
    except Exception as e:
        details.append(f"Trend Strength Error {e}")
    if htf is not None and htf==trend:
        score+=5; details.append(f"1H Agrees {htf} +5"); tags.append("1H AGREE")
    elif htf is not None and htf!=trend:
        details.append(f"1H Conflict {htf} - Tag only, NOT block")
        tags.append("1H CONFLICT - Caution"); warnings.append(f"1H vs 15m {htf}")
    if htf4 is not None and htf4==trend:
        score+=5; details.append(f"4H Agrees {htf4} +5"); tags.append("4H AGREE"); smart_notes.append("4H Agree")
    elif htf4 is not None and htf4!=trend:
        details.append(f"4H Conflict {htf4} - Tag only, NOT block")
        tags.append("4H CONFLICT - Caution"); warnings.append(f"4H vs 15m {htf4}")
    pb = PAIR_BONUS.get(name, 0)
    if pb!=0:
        score+=pb; details.append(f"Pair Bonus {name} +{pb}")
    # --- SMART++ 2: HOT PAIR MEMORY - BONUS ONLY ---
    hot_score = PAIR_HOT_SCORE.get(name, 0)
    if hot_score >= 2:
        score+=5; details.append(f"HOT PAIR {name} - {hot_score} signals today +5 BONUS"); tags.append(f"HOT PAIR 🔥 {hot_score} today"); smart_notes.append("Hot Pair")
    elif hot_score == 1:
        details.append(f"Pair seen 1x today - Normal")
        tags.append("Pair 1x today")
    else:
        details.append(f"Pair fresh today - First signal")
    sess_label, is_prime = get_session_label(now)
    if is_prime:
        score+=5; details.append(f"{sess_label} +5 - PRIME"); tags.append("PRIME SESSION"); smart_notes.append(sess_label)
    else:
        details.append(f"{sess_label} - Tag only, still tradeable")
        tags.append(sess_label); smart_notes.append(sess_label)
    r=rsi_calc(c15,14)
    if trend=="BUY" and r>80: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    if trend=="SELL" and r<20: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    details.append(f"RSI {r:.1f}")
    if r>70 and trend=="BUY": tags.append("REVERSAL RISK - RSI High")
    if r<30 and trend=="SELL": tags.append("REVERSAL RISK - RSI Low")
    atr_val=atr_calc(h5,l5,c5,14)
    if atr_val<=0: atr_val=abs(price*0.001)
    atr_ratio = atr_val / price if price!=0 else 0.001
    if atr_ratio > 0.0015:
        threshold = 78
        tags.append("HIGH MOMENTUM - Volatile Day"); details.append(f"Adaptive Threshold 78 - Volatile")
    elif atr_ratio < 0.0008:
        threshold = 82
        tags.append("LOW VOLUME - Quiet Day"); details.append(f"Adaptive Threshold 82 - Quiet")
    else:
        threshold = 80
        tags.append("NORMAL MOMENTUM"); details.append(f"Adaptive Threshold 80 - Normal")
    last_8_range = max(h15[-8:]) - min(l15[-8:])
    is_chop = last_8_range / price < 0.004
    if is_chop:
        tags.append("CHOP - Tight Range"); details.append(f"CHOP Tag - Range {last_8_range:.5f} tight, still tradeable")
        smart_notes.append("Chop - Careful")
    else:
        details.append(f"Chop OK - Range {last_8_range:.5f}")
    if name=="XAUUSD" and atr_val>6.0:
        return {"fail":True,"score":score,"reason":f"Gold ATR too high {atr_val:.2f}","trend":trend,"details":details}
    last3=c5[-1]-c5[-4]; chase=False
    if trend=="BUY" and last3>atr_val*0.6:
        chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT"); tags.append("CHASE - Use Limit")
    elif trend=="SELL" and last3<-atr_val*0.6:
        chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT"); tags.append("CHASE - Use Limit")
    else: details.append("Anti-Chase OK")
    big=0
    for i in range(-4,-1):
        rng=h5[i]-l5[i]
        if rng>atr_val*0.9:
            if trend=="BUY" and c5[i]>c5[i-1]: big+=1
            if trend=="SELL" and c5[i]<c5[i-1]: big+=1
    exhausted=big>=3
    if exhausted: warnings.append("EXHAUSTED"); details.append("EXHAUSTION"); tags.append("EXHAUSTED")
    hi_prev=max(h15[-20:-1]); lo_prev=min(l15[-20:-1])
    bos_ok=(trend=="BUY" and c15[-1]>hi_prev) or (trend=="SELL" and c15[-1]<lo_prev)
    if bos_ok: score+=20; details.append("15m BOS +20")
    else: details.append("BOS 0")
    # --- SMART++ 3: STRUCTURE - DOUBLE TOP/BOTTOM + CLEAN BREAKOUT - BONUS/TAG ONLY ---
    structure_tag = "None"
    structure_bonus = 0
    try:
        # Look at last 20 highs for double top/bottom
        last_highs = h15[-20:]
        last_lows = l15[-20:]
        max_h = max(last_highs); min_l = min(last_lows)
        # Count touches near max_h
        touches_high = sum(1 for h in last_highs if abs(h - max_h)/max_h < 0.001)
        touches_low = sum(1 for l in last_lows if abs(l - min_l)/min_l < 0.001)
        if trend=="SELL" and touches_high >= 2:
            structure_tag = "DOUBLE TOP - Reversal Risk"
            tags.append("DOUBLE TOP - Reversal Risk"); details.append("Structure: Double Top - Tag only, NOT block")
            smart_notes.append("Double Top")
        elif trend=="BUY" and touches_low >= 2:
            structure_tag = "DOUBLE BOTTOM - Reversal Risk"
            tags.append("DOUBLE BOTTOM - Reversal Risk"); details.append("Structure: Double Bottom - Tag only, NOT block")
            smart_notes.append("Double Bottom")
        elif bos_ok:
            # Clean breakout - price broke high with no double top
            structure_tag = "CLEAN BREAKOUT"
            structure_bonus = 5
            score+=5; details.append("CLEAN BREAKOUT +5 BONUS"); tags.append("CLEAN BREAKOUT"); smart_notes.append("Clean Breakout")
        else:
            details.append("Structure: Normal")
    except Exception as e:
        details.append(f"Structure Error {e}")
    prev_low=min(l5[-20:-2]); prev_high=max(h5[-20:-2])
    sweep_ok=False; sweep_bonus=False; sweep_level="Local"
    y_high=None; y_low=None
    if df_1d is not None and len(df_1d)>=2:
        try:
            y_high=float(df_1d['High'].iloc[-2]); y_low=float(df_1d['Low'].iloc[-2])
        except: pass
    asia_high=max(h5[-40:-20]) if len(h5)>40 else None
    asia_low=min(l5[-40:-20]) if len(l5)>40 else None
    for idx in [-6,-5,-4,-3,-2]:
        sc=df_5m.iloc[idx]
        if trend=="BUY" and sc['Low']<prev_low and sc['Close']>prev_low:
            rng=sc['High']-sc['Low']
            if rng>0 and (sc['Close']-sc['Low'])/rng>0.5:
                sweep_ok=True
                if y_low and abs(sc['Low']-y_low)/price<0.003: sweep_bonus=True; sweep_level="Yesterday Low"
                elif asia_low and abs(sc['Low']-asia_low)/price<0.003: sweep_bonus=True; sweep_level="Asia Low"
                break
        if trend=="SELL" and sc['High']>prev_high and sc['Close']<prev_high:
            rng=sc['High']-sc['Low']
            if rng>0 and (sc['High']-sc['Close'])/rng>0.5:
                sweep_ok=True
                if y_high and abs(sc['High']-y_high)/price<0.003: sweep_bonus=True; sweep_level="Yesterday High"
                elif asia_high and abs(sc['High']-asia_high)/price<0.003: sweep_bonus=True; sweep_level="Asia High"
                break
    if sweep_ok:
        score+=15; details.append(f"Sweep +15 ({sweep_level})")
        tags.append(f"LIQUIDITY SWEEP {sweep_level}")
        if sweep_bonus:
            score+=15; details.append(f"SMART Sweep at {sweep_level} +15 BONUS"); smart_notes.append(f"Sweep {sweep_level}")
    else: details.append("Sweep 0")
    bos5=False
    for i in range(len(df_5m)-5, len(df_5m)-1):
        if trend=="BUY" and c5[i]>max(h5[i-5:i]): bos5=True
        if trend=="SELL" and c5[i]<min(l5[i-5:i]): bos5=True
    if bos5: score+=10; details.append("5m BOS +10")
    fvg_found=False; fvg_top=0; fvg_bottom=0; fvg_ok=False; fvg_inside=False
    for i in range(len(df_5m)-16, len(df_5m)-3):
        if i<2 or i+2>=len(df_5m): continue
        h1=h5[i]; l1=l5[i]; h3=h5[i+2]; l3=l5[i+2]
        if trend=="BUY" and h1<l3: fvg_bottom=h1; fvg_top=l3; fvg_found=True; break
        if trend=="SELL" and l1>h3: fvg_top=l1; fvg_bottom=h3; fvg_found=True; break
    current=c5[-1]
    if fvg_found:
        if fvg_bottom<=current<=fvg_top: fvg_inside=True; score+=20; details.append("FVG INSIDE +20"); fvg_ok=True
        elif abs(current-((fvg_top+fvg_bottom)/2))/current<=0.003: score+=10; details.append("FVG Near +10"); fvg_ok=True
        else: details.append("FVG Far")
    else: details.append("FVG 0")
    obs=find_obs(df_5m, trend); ob_ok=False; ob_type="None"; ob_top=0; ob_bottom=0; ob_mid=0; ob_inside=False; ob_quality="Weak"
    if obs:
        best=None; best_dist=99999
        for o in obs:
            dist=abs(o["mid"]-current)
            if trend=="BUY" and o["mid"]>current+atr_val*0.5: continue
            if trend=="SELL" and o["mid"]<current-atr_val*0.5: continue
            if dist<best_dist: best_dist=dist; best=o
        if best is not None:
            ob_ok=True; ob_type=best["type"]; ob_top=best["top"]; ob_bottom=best["bottom"]; ob_mid=best["mid"]
            ob_inside=ob_bottom<=current<=ob_top
            if "BREAKER" in ob_type: score+=20; ob_quality="STRONG Breaker"; details.append(f"BREAKER {ob_type} +20")
            elif "MITIGATION" in ob_type: score+=18; ob_quality="STRONG Mitigation"; details.append(f"MITIGATION {ob_type} +18")
            elif best.get("fresh",False): score+=15; ob_quality="FRESH OB"; details.append(f"OB {ob_type} FRESH +15")
            else: score+=10; ob_quality="OLD OB"; details.append(f"OB {ob_type} OLD +10")
            if ob_inside: score+=5; details.append("OB INSIDE +5")
    else: details.append("OB 0")
    last_c=df_5m.iloc[-1]; last_rng=last_c['High']-last_c['Low']; rej=False
    if last_rng>0:
        if trend=="BUY" and (last_c['Close']-last_c['Low'])/last_rng>0.4: rej=True
        if trend=="SELL" and (last_c['High']-last_c['Close'])/last_rng>0.4: rej=True
    if rej: score+=10; details.append("Rejection 5m +10")
    m1_ok=False
    if df_1m is not None and len(df_1m)>=3:
        try:
            m1_last=df_1m.iloc[-1]; m1_rng=m1_last['High']-m1_last['Low']
            if m1_rng>0:
                if trend=="BUY" and (m1_last['Close']-m1_last['Low'])/m1_rng>0.4: m1_ok=True
                if trend=="SELL" and (m1_last['High']-m1_last['Close'])/m1_rng>0.4: m1_ok=True
            if m1_ok: score+=10; details.append("1m Rejection CONFIRM +10 BONUS"); smart_notes.append("1m Confirm")
            else: details.append("1m No confirm")
        except: details.append("1m Error")
    repeat_count = PAIR_SIGNAL_COUNT.get(name, 0)
    if repeat_count >= 2:
        tags.append(f"REPEAT - {repeat_count}th signal today, be careful")
    # --- SMART++ 4: FRESHNESS - TAG ONLY, NEVER BLOCK ---
    freshness = 100
    freshness_label = "FRESH"
    try:
        last_time = PAIR_LAST_SIGNAL_TIME.get(name)
        if last_time:
            mins_ago = (time.time() - last_time) / 60
            if mins_ago < 5: freshness = 100; freshness_label = "FRESH"
            elif mins_ago < 15: freshness = 80; freshness_label = "FRESH"
            elif mins_ago < 30: freshness = 60; freshness_label = "STALE - Check entry"
            else: freshness = 100; freshness_label = "FRESH - New cycle"
            tags.append(f"{freshness_label} - Last signal {mins_ago:.0f}m ago")
            details.append(f"Freshness: {freshness_label} ({mins_ago:.0f}m ago)")
        else:
            tags.append("FRESH - First today")
            details.append("Freshness: FRESH - First today")
    except: pass
    raw_score=score; score=min(score,120)
    print(f"{name} {trend} Score {score}/120 Raw {raw_score} Thr {threshold} RSI {r:.1f} OB {ob_type} FVG {fvg_ok} 1m {m1_ok} Trend {trend_label} {trend_strength:.0f}% Struct {structure_tag} Fresh {freshness_label} Hot {hot_score} Tags {tags}", flush=True)
    if score < threshold - 10:
        return {"fail":True,"score":score,"reason":f"Score {score} < Watch {threshold-10}","trend":trend,"details":details,"threshold":threshold}
    if score < threshold:
        return {"watch":True,"score":score,"trend":trend,"details":details,"symbol":name,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"smart_notes":smart_notes,"ob_type":ob_type,"tags":tags,"threshold":threshold,"sess_label":sess_label,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label}
    conf=sum([1 for x in [bos_ok,sweep_ok,fvg_ok,ob_ok] if x])
    if score>=90 and conf>=3 and not chase and (fvg_inside or ob_inside) and rej and m1_ok:
        tier="S-TIER SCALP"; tier_label="S"; take_it="YES - BEST SIGNAL - Take it"
    elif score>=80 and conf>=3 and not chase:
        tier="A-TIER SCALP"; tier_label="A"; take_it="YES - SCALP 0.01 only"
    elif score>=threshold and conf>=2:
        tier="B-TIER SCALP"; tier_label="B"; take_it="CAUTION - Smaller / Skip if unsure"
    else:
        return {"fail":True,"score":score,"reason":f"Quality Gate FAIL - Need 2/4, got {conf}/4","trend":trend,"details":details,"threshold":threshold}
    if ob_ok and fvg_found:
        if ob_inside or abs(ob_mid-current)<abs(((fvg_top+fvg_bottom)/2)-current):
            entry=ob_mid; entry_type=f"OB {ob_type}"
        else:
            entry=(fvg_top+fvg_bottom)/2; entry_type="FVG"
    elif ob_ok: entry=ob_mid; entry_type=f"OB {ob_type}"
    elif fvg_found: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG"
    else: entry=current; entry_type="Pullback"
    order_type="BUY LIMIT" if trend=="BUY" else "SELL LIMIT"
    sl_mult = 1.0 if atr_ratio>0.0015 else (0.7 if atr_ratio<0.0008 else 0.8)
    if trend=="BUY":
        inval=ob_bottom if ob_ok else (fvg_bottom if fvg_found else entry-atr_val)
        sl=max(inval-atr_val*0.2, entry-atr_val*sl_mult)
        risk=entry-sl
        if risk<atr_val*0.4: sl=entry-atr_val*0.7; risk=entry-sl
    else:
        inval=ob_top if ob_ok else (fvg_top if fvg_found else entry+atr_val)
        sl=min(inval+atr_val*0.2, entry+atr_val*sl_mult)
        risk=sl-entry
        if risk<atr_val*0.4: sl=entry+atr_val*0.7; risk=sl-entry
    if risk<=0: return {"fail":True,"score":score,"reason":"Risk <=0","trend":trend}
    max_r=MAX_RISK.get(name,9999)
    if risk>max_r: return {"fail":True,"score":score,"reason":f"SL Cap {risk:.5f} > {max_r}","trend":trend,"details":details}
    if risk/entry>0.01: return {"fail":True,"score":score,"reason":"SL too wide","trend":trend,"details":details}
    if trend=="BUY": tp1=entry+risk*0.8; tp2=entry+risk*1.5
    else: tp1=entry-risk*0.8; tp2=entry-risk*1.5
    conf_pct=round((score/120)*100,1)
    return {"symbol":name,"trend":trend,"order_type":order_type,"entry":round(float(entry),5),"sl":round(float(sl),5),"tp1":round(float(tp1),5),"tp2":round(float(tp2),5),"score":score,"confidence":conf_pct,"details":details,"tier":tier,"tier_label":tier_label,"take_it":take_it,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"entry_type":entry_type,"ob_type":ob_type,"ob_quality":ob_quality,"ob_top":round(float(ob_top),5) if ob_ok else 0,"ob_bottom":round(float(ob_bottom),5) if ob_ok else 0,"warnings":warnings,"chase":chase,"fvg_inside":fvg_inside,"rejection":rej,"inval":round(float(sl),5),"smart_notes":smart_notes,"sl_mult":sl_mult,"sweep_level":sweep_level if sweep_ok else "None","m1_ok":m1_ok,"tags":tags,"threshold":threshold,"sess_label":sess_label,"is_chop":is_chop,"is_prime":is_prime,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label,"freshness_pct":freshness,"hot_score":hot_score}

def bot_loop():
    send_telegram("V35.4 SMART++ LIVE - NO REDUCTION GUARANTEE\n12 Pairs | Adaptive 78-82 | S-TIER 90+ / A-TIER 80+ / B-TIER / WATCH\nSMART++: HOT Pair +5 | STRONG Trend +5 | CLEAN Breakout +5 | All BONUS ONLY\nTAGS NOT BLOCKS: Weak Trend / Double Top / Cold / Stale = Tag Only\nSCALPER: TP 0.8R + 1.5R | Max Hold 20m | 1m Confirm\nSessions: 09-21 EAT - PRIME vs CHOP Label | GOLD 11-19\nTEST MODE: Weekend dry-run every 60s")
    sent=set(); last_hunt=time.time(); last_sent_time={}; last_debug=time.time(); debug_reasons={}
    watch_sent=set()
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz); hour=now.hour+now.minute/60.0
            if now.weekday()>=5:
                print(f"WEEKEND {now} - Market closed, but TEST MODE: Doing dry-run analysis", flush=True)
                try:
                    test_pairs = ["EURUSD", "GBPUSD", "XAUUSD"]
                    pick = test_pairs[now.minute % len(test_pairs)]
                    ysym = SYMBOLS[pick]
                    print(f"TEST ANALYSIS: Checking {pick}...", flush=True)
                    res = analyze(pick, ysym, now)
                    print(f"TEST ANALYSIS {pick}: {res}", flush=True)
                    if now.minute % 30 == 0 and now.second < 65:
                        if res and not res.get("fail"):
                            send_telegram(f"TEST MODE - Weekend Dry Run\n\nPair: {pick}\nScore: {res.get('score')}/120\nTier: {res.get('tier')}\nTrend: {res.get('trend')} | Trend Strength: {res.get('trend_strength')}% {res.get('trend_label')}\nStructure: {res.get('structure_tag')} | Fresh: {res.get('freshness')}\nThis is TEST only, market closed. Real signals start Monday 09:00 EAT.")
                        elif res and res.get("watch"):
                            send_telegram(f"TEST MODE - Weekend Dry Run\n\nPair: {pick}\nScore: {res.get('score')}/120 - WATCH\nTrend: {res.get('trend')}\nThis is TEST only. Real signals start Monday 09:00 EAT.")
                        else:
                            send_telegram(f"TEST MODE - Weekend Dry Run\n\nPair: {pick}\nScore: {res.get('score',0)}/120 - {res.get('reason','No setup')}\nBot is analyzing correctly. Real signals start Monday 09:00 EAT.")
                except Exception as e:
                    print(f"TEST FAILED: {e}", flush=True); traceback.print_exc()
                time.sleep(60); continue
            if not (9.0<=hour<21.0):
                print(f"OUT OF SESSION {now} - alive, sleeping 60s", flush=True); time.sleep(60); continue
            if is_news_time(now):
                print(f"NEWS TIME {now} - blocked, sleeping", flush=True); time.sleep(300); continue
            sess_label, is_prime = get_session_label(now)
            print(f"V35.4 SMART++ checking {now} Session {sess_label}", flush=True)
            found=0; best_score=0; best_pair="None"
            for name, ysym in SYMBOLS.items():
                try:
                    if name in PAIR_COOLDOWN_UNTIL and time.time() < PAIR_COOLDOWN_UNTIL[name]:
                        print(f"{name} in MEMORY cooldown", flush=True); continue
                    if name=="XAUUSD" and not (11.0<=hour<19.0): continue
                    print(f"Checking {name}...", flush=True)
                    res=analyze(name, ysym, now)
                    if res and res.get("watch"):
                        wkey=f"{name}-{res['trend']}"
                        if wkey not in watch_sent:
                            watch_sent.add(wkey)
                            tags_txt=" | ".join(res.get('tags',[])) or "Getting ready"
                            send_telegram(f"WATCH - Score {res['score']}/120 (Need {res['threshold']})\n\nPair: {name}\nDirection: {res['trend']}\nLive: {res['live_price']}\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSession: {res['sess_label']}\nTrend: {res.get('trend_label')} {res.get('trend_strength')}%\nStructure: {res.get('structure_tag')} | Fresh: {res.get('freshness')}\nTags: {tags_txt}\n\n" + "\n".join(res['details']) + "\n\nNot a trade yet. Wait for S/A Tier.")
                        continue
                    if res and not res.get("fail"):
                        found+=1; last_hunt=time.time()
                        PAIR_SIGNAL_COUNT[name] = PAIR_SIGNAL_COUNT.get(name,0)+1
                        PAIR_HOT_SCORE[name] = PAIR_HOT_SCORE.get(name,0)+1
                        PAIR_LAST_SIGNAL_TIME[name] = time.time()
                        cool=f"{name}-{res['trend']}-{res['tier_label']}"
                        if cool in last_sent_time and (time.time()-last_sent_time[cool])<1200: continue
                        key=f"{name}-{res['trend']}-{res['entry']}-{res['tier_label']}"
                        if key in sent: continue
                        sent.add(key); last_sent_time[cool]=time.time()
                        LAST_SIGNAL_PRICE[key] = {"entry": res['entry'], "sl": res['sl'], "time": time.time(), "symbol": name, "trend": res['trend']}
                        warn_txt="\n".join([f"WARNING: {w}" for w in res.get('warnings',[])]) if res.get('warnings') else "No warnings"
                        smart_txt=", ".join(res.get('smart_notes',[])) or "None"
                        tags_txt=" | ".join(res.get('tags',[])) or "None"
                        # SMART++ LOT ADVISOR - Based on Tier + Trend + Hot + Fresh
                        if res['tier_label']=="S" and res.get('trend_label')=="STRONG":
                            lot_advice="Lot: 0.01 - BEST SIGNAL - STRONG + S-TIER"
                        elif res['tier_label']=="S":
                            lot_advice="Lot: 0.01 - S-TIER"
                        elif res['tier_label']=="A":
                            lot_advice="Lot: 0.01 ONLY"
                        else:
                            lot_advice="Lot: 0.01 - B-TIER - Consider skipping if unsure"
                        if res.get('is_chop'): lot_advice += "\nCHOP TAG - Half risk or skip"
                        if res.get('trend_label')=="WEAK": lot_advice += "\nWEAK TREND - Move to BE fast"
                        msg=f"{res['tier']} - Score {res['score']}/120 ({res['confidence']}%) - {res['sess_label']}\nThreshold: {res['threshold']}\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder: {res['order_type']}\nEntry Type: {res['entry_type']} - {res['ob_quality']}\nLive: {res['live_price']}\nEntry: {res['entry']}\nOB: {res['ob_bottom']} - {res['ob_top']} ({res['ob_type']})\nSL: {res['sl']} (Tight Scalp x{res['sl_mult']})\nTP1: {res['tp1']} (0.8R - Close 50% + BE)\nTP2: {res['tp2']} (1.5R - Close 50%)\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSweep: {res['sweep_level']} | 1m Confirm: {res['m1_ok']}\nSmart: {smart_txt}\nTags: {tags_txt}\nSMART++: Trend {res.get('trend_label')} {res.get('trend_strength')}% | Structure: {res.get('structure_tag')} | Fresh: {res.get('freshness')} ({res.get('freshness_pct')}%) | Hot: {res.get('hot_score')} today\n\n" + "\n".join(res['details']) + f"\n\nWARNINGS:\n{warn_txt}\n\nDECISION: {res['take_it']}\n\nSCALPER RULES:\n{lot_advice}\nTP1: Close 50%, MOVE SL TO ENTRY IMMEDIATELY\nTP2: Close 50%\nMAX HOLD 20 MINS - If not TP1 in 20m, EXIT\nIf closes past {res['inval']}, exit = Pair paused 2h\nLIMIT not filled 10m = CANCEL"
                        send_telegram(msg)
                    else:
                        sc=res.get("score",0) if res else 0
                        if sc>best_score: best_score=sc; best_pair=name
                        debug_reasons[name]=f"{res.get('trend','?')} {sc}/120 - {res.get('reason','Unknown')}"
                        print(f"{name}: {sc}/120 - {res.get('reason')}", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True); traceback.print_exc()
            if (time.time()-last_debug)>=1800:
                dbg="\n".join([f"{k}: {v}" for k,v in debug_reasons.items()])
                send_telegram(f"V35.4 Debug - Alive - {sess_label}\n\n{dbg}\nBest: {best_pair} {best_score}/120\nEAT {now.strftime('%H:%M')}")
                last_debug=time.time(); debug_reasons={}; watch_sent.clear()
            if found==0 and (time.time()-last_hunt)>=1800:
                send_telegram(f"Still Hunting SCALPS - V35.4 SMART++...\n\nNo S/A yet. Session: {sess_label}\nBest: {best_pair} - {best_score}/120\nEAT {now.strftime('%H:%M')}")
                last_hunt=time.time()
            if len(sent)>200: sent.clear()
            gc.collect(); print("Sleeping 60s...", flush=True); time.sleep(60)
        except Exception as e:
            print(f"Loop crash, retry 10s: {e}", flush=True); traceback.print_exc(); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    print("V35.4 SMART++ - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
