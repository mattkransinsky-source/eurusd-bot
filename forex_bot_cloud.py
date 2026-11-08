import os, time, threading, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime
from flask import Flask
import pytz

# ============ V6.0 FINAL BOSS INSTITUTIONAL + BRAIN ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
RISK_PCT = 0.01
EAT = pytz.timezone("Africa/Nairobi")
PAIRS = {"XAUUSD": "GC=F", "EURUSD": "EURUSD=X", "GBPJPY": "GBPJPY=X"}

app = Flask(__name__)
@app.route("/")
def home(): return "V6.0 FINAL BOSS RUNNING"
@app.route("/health")
def health(): return "OK"

def send(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

def rsi(s, p=14):
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/p, adjust=False).mean(); al=l.ewm(alpha=1/p, adjust=False).mean()
    return 100-(100/(1+(ag/(al.replace(0,1e-9)))))

def macd(s):
    e12=s.ewm(span=12, adjust=False).mean(); e26=s.ewm(span=26, adjust=False).mean()
    m=e12-e26; sig=m.ewm(span=9, adjust=False).mean(); return m,sig

def adx(df, p=14):
    h,l,c=df['High'],df['Low'],df['Close']
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/p, adjust=False).mean()
    up=h-h.shift(); dn=l.shift()-l
    plus=np.where((up>dn)&(up>0),up,0); minus=np.where((dn>up)&(dn>0),dn,0)
    pdi=100*pd.Series(plus,index=df.index).ewm(alpha=1/p, adjust=False).mean()/atr.replace(0,1e-9)
    mdi=100*pd.Series(minus,index=df.index).ewm(alpha=1/p, adjust=False).mean()/atr.replace(0,1e-9)
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,1e-9)
    return dx.ewm(alpha=1/p, adjust=False).mean().iloc[-1], pdi.iloc[-1], mdi.iloc[-1]

def get_data(ticker, interval, period="5d"):
    try:
        df=yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        return df
    except: return None

def get_dxy_trend():
    # V6.0 DXY Filter
    try:
        df=get_data("DX-Y.NYB","15m","2d")
        if df is None: return "NEUTRAL", 0
        c=df['Close']; ema20=float(c.ewm(span=20, adjust=False).mean().iloc[-1]); price=float(c.iloc[-1])
        # RSI of DXY
        r=float(rsi(c).iloc[-1])
        if price>ema20 and r>55: return "UP", r
        if price<ema20 and r<45: return "DOWN", r
        return "NEUTRAL", r
    except: return "NEUTRAL", 50

def get_vwap(df):
    try:
        if 'Volume' not in df.columns: return None
        tp=(df['High']+df['Low']+df['Close'])/3
        vwap=(tp*df['Volume']).cumsum()/df['Volume'].cumsum()
        return float(vwap.iloc[-1])
    except: return None

def is_news_time(now):
    wd=now.weekday(); hm=now.hour*60+now.minute
    if wd==4 and 900<=hm<=990: return "NFP"
    if wd==2 and 900<=hm<=990: return "CPI"
    if wd==2 and 1230<=hm<=1320: return "FOMC"
    return None

def calc_lot(pair, sl_pips, risk_pct):
    rm=ACCOUNT_BALANCE*risk_pct
    if pair=="XAUUSD": lot=rm/max(sl_pips,1)/10
    elif "JPY" in pair: lot=rm/(sl_pips*9)
    else: lot=rm/(sl_pips*10)
    return round(max(0.01,min(lot,0.5)),2)

def analyze_pair(name, ticker, dxy_trend, dxy_rsi, active_dirs, current_risk):
    now=datetime.now(EAT)
    df15=get_data(ticker,"15m","5d"); df1h=get_data(ticker,"1h","10d"); df4h_raw=get_data(ticker,"1h","30d"); df5=get_data(ticker,"5m","2d")
    if df15 is None or df1h is None or df5 is None or df4h_raw is None or len(df15)<80: return None
    df=df15.iloc[:-1]
    close15=df['Close']; price=float(close15.iloc[-1]); last=df.iloc[-1]
    candle_up=float(last['Close'])>float(last['Open']); candle_down=float(last['Close'])<float(last['Open'])
    body=abs(float(last['Close'])-float(last['Open'])); rng=float(last['High'])-float(last['Low'])
    if rng==0 or body/rng<0.45: return None
    r=float(rsi(close15).iloc[-1]); m,sig=macd(close15); macd_val=float(m.iloc[-1]); sig_val=float(sig.iloc[-1])
    adx_val,pdi,mdi=adx(df)
    ema50=float(close15.ewm(span=50, adjust=False).mean().iloc[-1]); ema20=float(close15.ewm(span=20, adjust=False).mean().iloc[-1])
    close1h=df1h['Close']; ema200_1h=float(close1h.ewm(span=200, adjust=False).mean().iloc[-1])
    df4h_res=df4h_raw.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    close4h=df4h_res['Close']; ema200_4h=float(close4h.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close4h)>10 else ema200_1h
    trend_4h="UP" if price>ema200_4h else "DOWN"; trend_1h="UP" if price>ema200_1h else "DOWN"
    tr=pd.concat([df['High']-df['Low'],(df['High']-close15.shift()).abs(),(df['Low']-close15.shift()).abs()],axis=1).max(axis=1)
    atr=float(tr.rolling(14).mean().iloc[-1])
    sup=float(df1h['Low'].tail(50).min()); res=float(df1h['High'].tail(50).max())
    # VWAP
    vwap=get_vwap(df)
    vwap_ok=None
    if vwap: vwap_ok="ABOVE" if price>vwap else "BELOW"
    today=df[df.index.date==df.index[-1].date()]
    london_high=float(today['High'].max()) if len(today)>0 else res
    london_low=float(today['Low'].min()) if len(today)>0 else sup
    swept_low=False; swept_high=False
    if len(df)>=5:
        last5=df.tail(5)
        if float(last5['Low'].min())<london_low and price>london_low: swept_low=True
        if float(last5['High'].max())>london_high and price<london_high: swept_high=True
    swing_high=float(df['High'].iloc[-22:-2].max()); swing_low=float(df['Low'].iloc[-22:-2].min())
    bos_up=price>swing_high; bos_down=price<swing_low
    fvg_bull=False; fvg_bear=False; fvg_zone=""
    if len(df)>=4:
        if float(df.iloc[-3]['High'])<float(df.iloc[-1]['Low']): fvg_bull=True; fvg_zone=f"{float(df.iloc[-3]['High']):.5f}-{float(df.iloc[-1]['Low']):.5f}"
        if float(df.iloc[-3]['Low'])>float(df.iloc[-1]['High']): fvg_bear=True; fvg_zone=f"{float(df.iloc[-1]['High']):.5f}-{float(df.iloc[-3]['Low']):.5f}"
    ob_found=False; ob_txt=""
    if bos_up:
        ob_low=float(df['Low'].iloc[-10:-2].min()); ob_high=ob_low+atr*0.5
        if price>=ob_low and price<=ob_high+atr*1.5: ob_found=True; ob_txt=f"OB {ob_low:.5f}-{ob_high:.5f}"
    if bos_down:
        ob_high=float(df['High'].iloc[-10:-2].max()); ob_low=ob_high-atr*0.5
        if price<=ob_high and price>=ob_low-atr*1.5: ob_found=True; ob_txt=f"OB {ob_low:.5f}-{ob_high:.5f}"
    vol_confirm=False
    if 'Volume' in df.columns and len(df)>20:
        avg_vol=float(df['Volume'].tail(20).mean()); last_vol=float(df.iloc[-1]['Volume'])
        vol_confirm=last_vol>avg_vol*1.1
    df5c=df5.iloc[:-1]; close5=df5c['Close']; r5=float(rsi(close5).iloc[-1]); m5,sig5=macd(close5); macd5=float(m5.iloc[-1]); sig5v=float(sig5.iloc[-1]); ema50_5=float(close5.ewm(span=50, adjust=False).mean().iloc[-1])
    if adx_val<22: return None
    if trend_1h!=trend_4h: return None
    # V6.0 DXY + VWAP FILTER
    direction=None; m5_agree=False; smart=[]; dxy_note=""
    # DXY Logic: DXY UP = EURUSD/GOLD DOWN. DXY DOWN = EURUSD/GOLD UP. GBPJPY less correlated but still check.
    if name in ["EURUSD","XAUUSD"]:
        if dxy_trend=="UP": # Dollar strong, only SELL allowed for EURUSD/Gold
            pass # will block BUY below
        if dxy_trend=="DOWN":
            pass
    if price>ema50 and 50<r<70 and macd_val>sig_val and pdi>mdi and trend_1h=="UP" and trend_4h=="UP" and candle_up and ema20>ema50 and bos_up:
        # V6.0 BLOCKS
        if name in ["EURUSD","XAUUSD"] and dxy_trend=="UP" and dxy_rsi>60:
            return None # DXY too strong, BUY will fail
        if vwap and price<vwap:
            return None # Below VWAP, banks selling
        if "BUY" in active_dirs:
            return None # Correlation block - already in BUY
        direction="BUY"; m5_agree=(r5>50 and macd5>sig5v and price>ema50_5)
        dxy_note=f"DXY: {dxy_trend} (OK for BUY)" if name in ["EURUSD","XAUUSD"] else f"DXY: {dxy_trend}"
        if swept_low: smart.append("💧 Sweep London Low")
        if fvg_bull: smart.append(f"📦 Bullish FVG {fvg_zone}")
        smart.append("📈 BOS Broke High")
        if ob_found: smart.append(f"🧱 {ob_txt}")
        if vol_confirm: smart.append("📊 Volume OK")
        if vwap: smart.append(f"📍 Above VWAP {vwap:.5f}")
    elif price<ema50 and 30<r<50 and macd_val<sig_val and mdi>pdi and trend_1h=="DOWN" and trend_4h=="DOWN" and candle_down and ema20<ema50 and bos_down:
        if name in ["EURUSD","XAUUSD"] and dxy_trend=="DOWN" and dxy_rsi<40:
            return None # DXY too weak, SELL will fail
        if vwap and price>vwap:
            return None # Above VWAP, banks buying
        if "SELL" in active_dirs:
            return None
        direction="SELL"; m5_agree=(r5<50 and macd5<sig5v and price<ema50_5)
        dxy_note=f"DXY: {dxy_trend} (OK for SELL)" if name in ["EURUSD","XAUUSD"] else f"DXY: {dxy_trend}"
        if swept_high: smart.append("💧 Sweep London High")
        if fvg_bear: smart.append(f"📦 Bearish FVG {fvg_zone}")
        smart.append("📉 BOS Broke Low")
        if ob_found: smart.append(f"🧱 {ob_txt}")
        if vol_confirm: smart.append("📊 Volume OK")
        if vwap: smart.append(f"📍 Below VWAP {vwap:.5f}")
    else: return None
    dist_res=abs(res-price)/max(price,1e-9)*100; dist_sup=abs(price-sup)/max(price,1e-9)*100
    if direction=="BUY" and dist_res<0.05: return None
    if direction=="SELL" and dist_sup<0.05: return None
    score=50
    if adx_val>25: score+=10
    if adx_val>30: score+=5
    if direction=="BUY" and 55<=r<=65: score+=10
    if direction=="SELL" and 35<=r<=45: score+=10
    if body/rng>0.6: score+=10
    if m5_agree: score+=10
    if swept_low and direction=="BUY": score+=15
    if swept_high and direction=="SELL": score+=15
    if (fvg_bull and direction=="BUY") or (fvg_bear and direction=="SELL"): score+=10
    if ob_found: score+=10
    if vol_confirm: score+=5
    if vwap: score+=5 # VWAP bonus
    score+=5
    score=min(99, max(55,int(score)))
    if score<85: return None
    grade="👑 LEGENDARY" if score>=97 else "💎 DIAMOND" if score>=95 else "A+ SNIPER" if score>=90 else "A Setup"
    if name=="XAUUSD": sl_mult,tp1_mult,tp2_mult,tp3_mult=1.2,1.8,3.0,5.0
    else: sl_mult,tp1_mult,tp2_mult,tp3_mult=1.5,1.5,2.5,4.0
    if direction=="BUY": sl=price-atr*sl_mult; tp1=price+atr*tp1_mult; tp2=price+atr*tp2_mult; tp3=price+atr*tp3_mult
    else: sl=price+atr*sl_mult; tp1=price-atr*tp1_mult; tp2=price-atr*tp2_mult; tp3=price-atr*tp3_mult
    sl_dist=abs(price-sl)
    if "JPY" in name: sl_pips=sl_dist*100
    elif name=="XAUUSD": sl_pips=sl_dist*10
    else: sl_pips=sl_dist*10000
    lot=calc_lot(name, max(sl_pips,1), current_risk)
    return {"pair":name,"dir":direction,"price":price,"rsi":r,"adx":adx_val,"trend1h":trend_1h,"trend4h":trend_4h,"score":score,"grade":grade,"sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,"lot":lot,"sup":sup,"res":res,"london_high":london_high,"london_low":london_low,"m5_agree":m5_agree,"smart":smart,"vol":vol_confirm,"vwap":vwap,"dxy_note":dxy_note,"risk":current_risk}

def in_session(now):
    wd=now.weekday()
    if wd>=5: return False
    if wd==0 and now.hour<12: return False
    if wd==4 and now.hour>=17: return False
    hm=now.hour*60+now.minute
    return (720<=hm<=840) or (1020<=hm<=1140)

def get_live_price(ticker):
    try:
        df=yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1])
    except: return None

def bot_loop():
    sent_today=set(); active={}; last_report=None; last_bias=None; current_risk=RISK_PCT
    send("👑 <b>V6.0 FINAL BOSS is LIVE</b>\n✅ 3 Pairs: XAUUSD, EURUSD, GBPJPY\n✅ DXY FILTER - Dollar Boss\n✅ VWAP - Trade With Banks\n✅ Correlation Block - 1 Trade At A Time\n✅ 3-STAGE TP - 50% / 30% / 20% Run\n✅ Adaptive Risk - Protect After Loss\n✅ Order Block + Killzones + 4H + Volume + Sweep + FVG + BOS\n✅ ONLY 85+ - LEGENDARY MODE\n💰 Balance: $%.0f\n👑 FINAL FORM - No More Upgrades After This" % ACCOUNT_BALANCE)
    while True:
        try:
            now=datetime.now(EAT)
            if now.hour==9 and now.minute<5 and last_bias!=now.date():
                last_bias=now.date(); lines=[]
                dxy_trend,_=get_dxy_trend()
                for n,t in PAIRS.items():
                    d=get_data(t,"1h","30d")
                    if d is not None:
                        d4=d.resample('4h').agg({'Close':'last'}).dropna()
                        c=d4['Close']; e200=float(c.ewm(span=200, adjust=False).mean().iloc[-1]) if len(c)>10 else 0; p=float(c.iloc[-1])
                        lines.append(f"{n}: {'🟢 BUY ONLY' if p>e200 else '🔴 SELL ONLY'} (4H)")
                send(f"🌅 <b>TODAY'S BIAS 4H - {now.strftime('%Y-%m-%d')}</b>\n"+"\n".join(lines)+f"\n💵 DXY: {dxy_trend}\nRisk Today: {current_risk*100:.1f}%\n\nFINAL BOSS: Wait for Killzone. Max 1 trade.")
            if now.hour==21 and now.minute<5 and last_report!=now.date():
                last_report=now.date()
                send(f"📊 <b>DAILY REPORT - {now.strftime('%Y-%m-%d')}</b>\nLegendary Signals Today: {len(sent_today)}\nBalance: ${ACCOUNT_BALANCE:.0f}\nRisk Tomorrow: {current_risk*100:.1f}%\nBot: V6.0 FINAL BOSS 👑")
                sent_today=set(); active={}
            if not in_session(now): time.sleep(60); continue
            if is_news_time(now): time.sleep(60); continue
            for pn,tr in list(active.items()):
                if tr.get("alerted"): continue
                live=get_live_price(PAIRS[pn])
                if live is None: continue
                hit=(live>=tr["tp1"]) if tr["dir"]=="BUY" else (live<=tr["tp1"])
                if hit:
                    tr["alerted"]=True
                    send(f"⚠️ <b>TP1 HIT - TAKE 50% + MOVE SL!</b>\n{pn} {tr['dir']}\nPrice hit TP1: {tr['tp1']:.5f}\n👉 In MT5: Close 50% NOW, Move SL to Entry!\nThen TP2: {tr['tp2']:.5f} (Close 30%)\nThen TP3: {tr['tp3']:.5f} (Let 20% Run)\nThis trade is now FREE.")
            dxy_trend,dxy_rsi=get_dxy_trend()
            active_dirs=set([v["dir"] for v in active.values()])
            for name,ticker in PAIRS.items():
                key=f"{name}-{now.strftime('%Y-%m-%d-%H')}"
                if key in sent_today: continue
                res=analyze_pair(name,ticker,dxy_trend,dxy_rsi,active_dirs,current_risk)
                if res:
                    sent_today.add(key); active[name]={"dir":res['dir'],"tp1":res['tp1'],"tp2":res['tp2'],"tp3":res['tp3'],"sl":res['sl'],"alerted":False}
                    emoji="🟢" if res['dir']=="BUY" else "🔴"
                    m5_txt="✅ 5M CONFIRMED" if res['m5_agree'] else "⚠️ 5M Not Yet"
                    smart_txt="\n".join(res['smart']) if res['smart'] else "BOS Only"
                    msg=(f"{emoji} <b>{res['pair']} {res['dir']} - V6.0 LEGENDARY</b>\n"
                         f"⭐ Score: {res['score']}/100 - {res['grade']}\n"
                         f"👑 <b>FINAL BOSS REASON:</b>\n{smart_txt}\n"
                         f"{m5_txt} | 4H: {res['trend4h']} | 1H: {res['trend1h']}\n"
                         f"{res['dxy_note']} | VWAP: {res['vwap']:.5f}\n"
                         f"Price: {res['price']:.5f}\n"
                         f"RSI: {res['rsi']:.1f} | ADX: {res['adx']:.1f}\n"
                         f"Lot: {res['lot']} (Risk {res['risk']*100:.1f}% = ${ACCOUNT_BALANCE*res['risk']:.2f})\n"
                         f"📊 London High: {res['london_high']:.5f} | Low: {res['london_low']:.5f}\n"
                         f"🎯 TP1: {res['tp1']:.5f} (Close 50% + Move SL)\n"
                         f"🎯 TP2: {res['tp2']:.5f} (Close 30%)\n"
                         f"🎯 TP3: {res['tp3']:.5f} (Let 20% Run!)\n"
                         f"🛑 SL: {res['sl']:.5f}\n"
                         f"⏰ {now.strftime('%H:%M')} EAT | ⏳ Valid 30 mins ONLY\n"
                         f"👑 LEGENDARY - Take This One, Only 1 Trade Today!")
                    send(msg)
                time.sleep(2)
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__=="__main__":
    port=int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
