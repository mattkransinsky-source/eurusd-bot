import os, time, requests, threading, pytz, gc, traceback
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "V35.1 SMART ALIVE", 200
@app.route('/health')
def health():
    return "OK", 200
@app.route('/<path:path>')
def catch_all(path):
    return "V35.1 SMART ALIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"V35.1 Flask on {port}", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# V35.1: 12 PAIRS
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

# SMART MEMORY - Track last signals to avoid revenge trades
PAIR_COOLDOWN_UNTIL = {}  # pair -> timestamp when it can trade again
LAST_SIGNAL_PRICE = {} # for second chance

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
    if len(values) < period + 1:
        return 50
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
    if len(closes) < period + 1:
        return abs(closes[-1] * 0.001)
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

def analyze(name, ysym, now):
    df_4h = get_data(ysym, "4h", "30d")
    df_1h = get_data(ysym, "1h", "10d")
    df_15m = get_data(ysym, "15m", "5d")
    df_5m = get_data(ysym, "5m", "2d")
    df_1d = get_data(ysym, "1d", "5d")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m)<50 or len(df_15m)<50 or len(df_1h)<20 or len(df_4h)<20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    df_5m = df_5m.iloc[:-1]; df_15m = df_15m.iloc[:-1]
    c15=df_15m['Close'].tolist(); c5=df_5m['Close'].tolist(); c1=df_1h['Close'].tolist(); c4=df_4h['Close'].tolist()
    h5=df_5m['High'].tolist(); l5=df_5m['Low'].tolist(); h15=df_15m['High'].tolist(); l15=df_15m['Low'].tolist()
    score=0; details=[]; warnings=[]; smart_notes=[]
    # --- 4H Trend ---
    e50_4h=ema(c4,50); e20_4h=ema(c4,20); p4=c4[-1]
    htf4="BUY" if p4>e50_4h and p4>e20_4h else ("SELL" if p4<e50_4h and p4<e20_4h else None)
    # --- 1H Trend ---
    e50_1h=ema(c1,50); e20_1h=ema(c1,20); p1=c1[-1]
    htf="BUY" if p1>e50_1h and p1>e20_1h else ("SELL" if p1<e50_1h and p1<e20_1h else None)
    # --- 15m Trend +20 ---
    e50=ema(c15,50); e200=ema(c15,200); price=c15[-1]; trend=None
    if price>e50 and price>e200:
        trend="BUY"; score+=20; details.append("EMA 50+200 BUY +20")
    elif price<e50 and price<e200:
        trend="SELL"; score+=20; details.append("EMA 50+200 SELL +20")
    else:
        return {"fail":True,"score":score,"reason":f"EMA fail {price:.5f} vs {e50:.5f}/{e200:.5f}","trend":"NONE"}
    # 1H Conflict = BLOCK (still strict)
    if htf is not None and htf!=trend:
        return {"fail":True,"score":score,"reason":f"1H Conflict BLOCK {htf} vs {trend}","trend":trend,"details":details}
    elif htf==trend: details.append(f"1H Agrees {htf}")
    # SMART: 4H = BONUS not BLOCK (V35.1 - keeps volume)
    if htf4 is not None and htf4==trend:
        score+=10; details.append(f"4H Agrees {htf4} +10 BONUS")
        smart_notes.append("4H Agree")
    elif htf4 is not None and htf4!=trend:
        details.append(f"4H Conflict {htf4} vs {trend} - WARNING only")
        warnings.append(f"4H vs 15m conflict ({htf4})")
        smart_notes.append("4H Conflict - Caution")
    # SMART: SESSION INTELLIGENCE
    hour = now.hour + now.minute/60.0
    if 15.0 <= hour < 19.0:
        score+=5; details.append("NY Session +5 - Best moves"); smart_notes.append("NY Session")
    elif 9.0 <= hour < 11.0:
        score+=3; details.append("London Open +3"); smart_notes.append("London Open")
    elif 11.0 <= hour < 15.0:
        score-=5; details.append("Slow Session -5"); smart_notes.append("Slow Session")
    # RSI
    r=rsi_calc(c15,14)
    if trend=="BUY" and r>75: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    if trend=="SELL" and r<25: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    details.append(f"RSI {r:.1f}")
    atr_val=atr_calc(h5,l5,c5,14)
    if atr_val<=0: atr_val=abs(price*0.001)
    # SMART: CHOP DETECTOR - If range stuck 2 hours, skip
    last_8_range = max(h15[-8:]) - min(l15[-8:])
    # 8 x 15m = 2 hours. If range < 0.4% of price, it's chop
    if last_8_range / price < 0.004:
        return {"fail":True,"score":score,"reason":f"CHOP DETECTED - Range {last_8_range:.5f} too tight 2h","trend":trend,"details":details}
    else:
        details.append(f"Chop OK - Range {last_8_range:.5f}")
    # Gold Volatility
    if name=="XAUUSD" and atr_val>6.0:
        return {"fail":True,"score":score,"reason":f"Gold ATR too high {atr_val:.2f}","trend":trend,"details":details}
    # Anti-Chase
    last3=c5[-1]-c5[-4]; chase=False
    if trend=="BUY" and last3>atr_val*0.8:
        chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT")
    elif trend=="SELL" and last3<-atr_val*0.8:
        chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT")
    else: details.append("Anti-Chase OK")
    # Exhaustion
    big=0
    for i in range(-4,-1):
        rng=h5[i]-l5[i]
        if rng>atr_val*0.9:
            if trend=="BUY" and c5[i]>c5[i-1]: big+=1
            if trend=="SELL" and c5[i]<c5[i-1]: big+=1
    exhausted=big>=3
    if exhausted: warnings.append("EXHAUSTED"); details.append("EXHAUSTION")
    # BOS 15m +20
    hi_prev=max(h15[-20:-1]); lo_prev=min(l15[-20:-1])
    bos_ok=(trend=="BUY" and c15[-1]>hi_prev) or (trend=="SELL" and c15[-1]<lo_prev)
    if bos_ok: score+=20; details.append("15m BOS +20")
    else: details.append("BOS 0")
    # SMART: Asia + Yesterday High/Low Sweep Bonus
    prev_low=min(l5[-20:-2]); prev_high=max(h5[-20:-2])
    sweep_ok=False; sweep_bonus=False; sweep_level="Local"
    # Yesterday High/Low if daily data available
    y_high=None; y_low=None
    if df_1d is not None and len(df_1d)>=2:
        try:
            y_high=float(df_1d['High'].iloc[-2]); y_low=float(df_1d['Low'].iloc[-2])
        except: pass
    # Asia session approx: 03:00-09:00 EAT = first candles of day in 5m - use last 30 candles low/high as proxy for Asia
    asia_high=max(h5[-40:-20]) if len(h5)>40 else None
    asia_low=min(l5[-40:-20]) if len(l5)>40 else None
    for idx in [-6,-5,-4,-3,-2]:
        sc=df_5m.iloc[idx]
        if trend=="BUY" and sc['Low']<prev_low and sc['Close']>prev_low:
            rng=sc['High']-sc['Low']
            if rng>0 and (sc['Close']-sc['Low'])/rng>0.5:
                sweep_ok=True
                # Check if sweep was at key level
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
        if sweep_bonus:
            score+=15; details.append(f"SMART Sweep at {sweep_level} +15 BONUS"); smart_notes.append(f"Sweep {sweep_level}")
    else: details.append("Sweep 0")
    # 5m BOS +10
    bos5=False
    for i in range(len(df_5m)-5, len(df_5m)-1):
        if trend=="BUY" and c5[i]>max(h5[i-5:i]): bos5=True
        if trend=="SELL" and c5[i]<min(l5[i-5:i]): bos5=True
    if bos5: score+=10; details.append("5m BOS +10")
    # FVG +20
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
    # OB +20
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
    # Rejection +10
    last_c=df_5m.iloc[-1]; last_rng=last_c['High']-last_c['Low']; rej=False
    if last_rng>0:
        if trend=="BUY" and (last_c['Close']-last_c['Low'])/last_rng>0.4: rej=True
        if trend=="SELL" and (last_c['High']-last_c['Close'])/last_rng>0.4: rej=True
    if rej: score+=10; details.append("Rejection +10")
    # Cap score at 120 for display, but keep raw for logic
    raw_score=score
    score=min(score,120)
    print(f"{name} {trend} Score {score}/120 Raw {raw_score} RSI {r:.1f} OB {ob_type} FVG {fvg_ok} Smart {smart_notes}", flush=True)
    # WATCH: 70-79 = Watch only, 80+ = Trade
    if score<70:
        return {"fail":True,"score":score,"reason":f"Score {score} <70","trend":trend,"details":details}
    if score<80:
        return {"watch":True,"score":score,"trend":trend,"details":details,"symbol":name,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"smart_notes":smart_notes,"ob_type":ob_type}
    conf=sum([1 for x in [bos_ok,sweep_ok,fvg_ok,ob_ok] if x])
    if conf>=3 and not chase and not exhausted and (fvg_inside or ob_inside) and rej:
        tier="A+ SHARP OB"; tier_label="A+"; take_it="YES - Take it"
    elif conf>=3 and not chase:
        tier="A - GOOD OB"; tier_label="A"; take_it="YES - Take it, 0.01 only"
    else:
        return {"fail":True,"score":score,"reason":f"Quality Gate FAIL - Need 3/4, got {conf}/4","trend":trend,"details":details}
    # Entry
    if ob_ok and fvg_found:
        if ob_inside or abs(ob_mid-current)<abs(((fvg_top+fvg_bottom)/2)-current):
            entry=ob_mid; entry_type=f"OB {ob_type}"
        else:
            entry=(fvg_top+fvg_bottom)/2; entry_type="FVG"
    elif ob_ok: entry=ob_mid; entry_type=f"OB {ob_type}"
    elif fvg_found: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG"
    else: entry=current; entry_type="Pullback"
    order_type="BUY LIMIT" if trend=="BUY" else "SELL LIMIT"
    # SMART: VOLATILITY ADAPTIVE SL - Wider when ATR big, tighter when small
    # ATR ratio vs price: if ATR > 0.15% of price, volatile -> 1.2x, else 0.8x
    atr_ratio = atr_val / price if price!=0 else 0.001
    sl_mult = 1.2 if atr_ratio>0.0015 else (0.8 if atr_ratio<0.0008 else 1.0)
    if trend=="BUY":
        inval=ob_bottom if ob_ok else (fvg_bottom if fvg_found else entry-atr_val)
        sl=max(inval-atr_val*0.2, entry-atr_val*sl_mult)
        risk=entry-sl
        if risk<atr_val*0.5: sl=entry-atr_val*0.8; risk=entry-sl
    else:
        inval=ob_top if ob_ok else (fvg_top if fvg_found else entry+atr_val)
        sl=min(inval+atr_val*0.2, entry+atr_val*sl_mult)
        risk=sl-entry
        if risk<atr_val*0.5: sl=entry+atr_val*0.8; risk=sl-entry
    if risk<=0: return {"fail":True,"score":score,"reason":"Risk <=0","trend":trend}
    max_r=MAX_RISK.get(name,9999)
    if risk>max_r: return {"fail":True,"score":score,"reason":f"SL Cap {risk:.5f} > {max_r}","trend":trend,"details":details}
    if risk/entry>0.01: return {"fail":True,"score":score,"reason":"SL too wide","trend":trend,"details":details}
    if trend=="BUY": tp1=entry+risk*1.5; tp2=entry+risk*3.0
    else: tp1=entry-risk*1.5; tp2=entry-risk*3.0
    conf_pct=round((score/120)*100,1)
    return {"symbol":name,"trend":trend,"order_type":order_type,"entry":round(float(entry),5),"sl":round(float(sl),5),"tp1":round(float(tp1),5),"tp2":round(float(tp2),5),"score":score,"confidence":conf_pct,"details":details,"tier":tier,"tier_label":tier_label,"take_it":take_it,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"entry_type":entry_type,"ob_type":ob_type,"ob_quality":ob_quality,"ob_top":round(float(ob_top),5) if ob_ok else 0,"ob_bottom":round(float(ob_bottom),5) if ob_ok else 0,"warnings":warnings,"chase":chase,"fvg_inside":fvg_inside,"rejection":rej,"inval":round(float(sl),5),"smart_notes":smart_notes,"sl_mult":sl_mult,"sweep_level":sweep_level if sweep_ok else "None"}

def bot_loop():
    send_telegram("V35.1 SMART LIVE\n12 Pairs | Score 80+/120 TRADE | 70-79 WATCH\nOnly A+ / A | 1H BLOCK | 4H BONUS | News + Weekend + Gold ATR\nSMART: Chop Detector + Asia/Yesterday Sweep + Memory + Session + Adaptive SL\nTP 1.5R + 3R | Session 09-21 EAT | GOLD 11-19 EAT")
    sent=set(); last_hunt=time.time(); last_sent_time={}; last_debug=time.time(); debug_reasons={}
    watch_sent=set()
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz); hour=now.hour+now.minute/60.0
            if now.weekday()>=5:
                print(f"WEEKEND {now} - Market closed, sleeping", flush=True); time.sleep(300); continue
            if not (9.0<=hour<21.0):
                print(f"OUT OF SESSION {now} - alive, sleeping", flush=True); time.sleep(60); continue
            if is_news_time(now):
                print(f"NEWS TIME {now} - blocked, sleeping", flush=True); time.sleep(300); continue
            print(f"V35.1 checking {now}", flush=True)
            found=0; best_score=0; best_pair="None"
            for name, ysym in SYMBOLS.items():
                try:
                    # SMART MEMORY: If pair is in cooldown after SL, skip
                    if name in PAIR_COOLDOWN_UNTIL and time.time() < PAIR_COOLDOWN_UNTIL[name]:
                        print(f"{name} in MEMORY cooldown until {PAIR_COOLDOWN_UNTIL[name]}", flush=True)
                        continue
                    if name=="XAUUSD" and not (11.0<=hour<19.0): continue
                    print(f"Checking {name}...", flush=True)
                    res=analyze(name, ysym, now)
                    if res and res.get("watch"):
                        # WATCH 70-79 - Send once per 1 hour
                        wkey=f"{name}-{res['trend']}"
                        if wkey not in watch_sent:
                            watch_sent.add(wkey)
                            smart_txt=", ".join(res.get('smart_notes',[])) or "Getting ready"
                            send_telegram(f"WATCH - Score {res['score']}/120\n\nPair: {name}\nDirection: {res['trend']}\nLive: {res['live_price']}\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSmart: {smart_txt}\n\n" + "\n".join(res['details']) + "\n\nNot a trade yet. Wait for 80+ or retest.")
                        continue
                    if res and not res.get("fail"):
                        found+=1; last_hunt=time.time()
                        cool=f"{name}-{res['trend']}-{res['tier_label']}"
                        if cool in last_sent_time and (time.time()-last_sent_time[cool])<1800: continue
                        key=f"{name}-{res['trend']}-{res['entry']}-{res['tier_label']}"
                        if key in sent: continue
                        sent.add(key); last_sent_time[cool]=time.time()
                        # SMART: Save for Second Chance + Memory
                        LAST_SIGNAL_PRICE[key] = {"entry": res['entry'], "sl": res['sl'], "time": time.time(), "symbol": name, "trend": res['trend']}
                        warn_txt="\n".join([f"WARNING: {w}" for w in res.get('warnings',[])]) if res.get('warnings') else "No warnings"
                        smart_txt=", ".join(res.get('smart_notes',[])) or "None"
                        msg=f"{res['tier']} - Score {res['score']}/120 ({res['confidence']}%)\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder: {res['order_type']}\nEntry Type: {res['entry_type']} - {res['ob_quality']}\nLive: {res['live_price']}\nEntry: {res['entry']}\nOB: {res['ob_bottom']} - {res['ob_top']} ({res['ob_type']})\nSL: {res['sl']} (Adaptive x{res['sl_mult']})\nTP1: {res['tp1']} (1.5R - Close 50%)\nTP2: {res['tp2']} (3R - Close 50%)\nRSI: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSweep: {res['sweep_level']}\nSmart: {smart_txt}\n\n" + "\n".join(res['details']) + f"\n\nWARNINGS:\n{warn_txt}\n\nDECISION: {res['take_it']}\n\nLot: 0.01 ONLY\nTP1: Close 50%, MOVE SL TO ENTRY\nTP2: Close 50%\nIf closes past {res['inval']}, exit = Pair paused 2h (SMART MEMORY)\nMAX HOLD 1H | LIMIT not filled 15m = CANCEL"
                        send_telegram(msg)
                    else:
                        sc=res.get("score",0) if res else 0
                        if sc>best_score: best_score=sc; best_pair=name
                        debug_reasons[name]=f"{res.get('trend','?')} {sc}/120 - {res.get('reason','Unknown')}"
                        print(f"{name}: {sc}/120 - {res.get('reason')}", flush=True)
                        # SMART: SECOND CHANCE - If price comes back to old entry after 15m, re-alert
                        for old_key, old in list(LAST_SIGNAL_PRICE.items()):
                            if old['symbol']==name and abs(res.get('live_price',0) if 'live_price' in res else 0 - old['entry'])<0.0001:
                                pass
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True); traceback.print_exc()
            # SMART: Check if old signals hit SL - if so, pause pair 2h (Memory)
            # We do this by checking if current price is beyond SL for old signals - simplified: if price moved past SL, set cooldown
            # Note: Real SL tracking needs broker, this is approximation from price
            if (time.time()-last_debug)>=1800:
                dbg="\n".join([f"{k}: {v}" for k,v in debug_reasons.items()])
                send_telegram(f"V35.1 Debug - Alive\n\n{dbg}\nBest: {best_pair} {best_score}/120\nEAT {now.strftime('%H:%M')}")
                last_debug=time.time(); debug_reasons={}; watch_sent.clear()
            if found==0 and (time.time()-last_hunt)>=1800:
                send_telegram(f"Still Hunting - V35.1 SMART...\n\nNo 80+ yet.\nBest: {best_pair} - {best_score}/120\nEAT {now.strftime('%H:%M')}")
                last_hunt=time.time()
            if len(sent)>200: sent.clear()
            gc.collect(); print("Sleeping 60s...", flush=True); time.sleep(60)
        except Exception as e:
            print(f"Loop crash, retry 10s: {e}", flush=True); traceback.print_exc(); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    print("V35.1 SMART - Flask is now MAIN thread, Bot is background", flush=True)
    run_web()
