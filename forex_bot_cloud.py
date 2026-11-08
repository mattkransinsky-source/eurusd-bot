import os, time, threading, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime
from flask import Flask
import pytz

# ============ V5.3 MANUAL PRO++ SMART MONEY ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
RISK_PCT = 0.01
EAT = pytz.timezone("Africa/Nairobi")

PAIRS = {"XAUUSD": "GC=F", "EURUSD": "EURUSD=X", "GBPJPY": "GBPJPY=X"}

app = Flask(__name__)
@app.route("/")
def home(): return "V5.3 SMART MONEY RUNNING"
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

def is_news_time(now):
    wd=now.weekday(); hm=now.hour*60+now.minute
    if wd==4 and 900<=hm<=990: return "NFP"
    if wd==2 and 900<=hm<=990: return "CPI"
    if wd==2 and 1230<=hm<=1320: return "FOMC"
    return None

def calc_lot(pair, sl_pips):
    rm=ACCOUNT_BALANCE*RISK_PCT
    if pair=="XAUUSD": lot=rm/max(sl_pips,1)/10
    elif "JPY" in pair: lot=rm/(sl_pips*9)
    else: lot=rm/(sl_pips*10)
    return round(max(0.01,min(lot,0.5)),2)

def analyze_pair(name, ticker):
    now=datetime.now(EAT)
    df15=get_data(ticker,"15m","5d"); df1h=get_data(ticker,"1h","10d"); df5=get_data(ticker,"5m","2d")
    if df15 is None or df1h is None or df5 is None or len(df15)<80: return None
    df=df15.iloc[:-1]
    close15=df['Close']; price=float(close15.iloc[-1]); last=df.iloc[-1]
    candle_up=float(last['Close'])>float(last['Open']); candle_down=float(last['Close'])<float(last['Open'])
    body=abs(float(last['Close'])-float(last['Open'])); rng=float(last['High'])-float(last['Low'])
    if rng==0 or body/rng<0.4: return None
    r=float(rsi(close15).iloc[-1]); m,sig=macd(close15); macd_val=float(m.iloc[-1]); sig_val=float(sig.iloc[-1])
    adx_val,pdi,mdi=adx(df)
    ema50=float(close15.ewm(span=50, adjust=False).mean().iloc[-1]); ema20=float(close15.ewm(span=20, adjust=False).mean().iloc[-1])
    close1h=df1h['Close']; ema200_1h=float(close1h.ewm(span=200, adjust=False).mean().iloc[-1])
    tr=pd.concat([df['High']-df['Low'],(df['High']-close15.shift()).abs(),(df['Low']-close15.shift()).abs()],axis=1).max(axis=1)
    atr=float(tr.rolling(14).mean().iloc[-1])
    sup=float(df1h['Low'].tail(50).min()); res=float(df1h['High'].tail(50).max())
    # === V5.3 SMART MONEY ===
    # 1. London High/Low + Sweep
    today=df[df.index.date==df.index[-1].date()]
    london_high=float(today['High'].max()) if len(today)>0 else res
    london_low=float(today['Low'].min()) if len(today)>0 else sup
    # Sweep: price went below London Low then closed back above = Bullish Sweep
    # Or went above London High then closed back below = Bearish Sweep
    swept_low=False; swept_high=False
    if len(df)>=5:
        last5=df.tail(5)
        if float(last5['Low'].min()) < london_low and price > london_low:
            swept_low=True
        if float(last5['High'].max()) > london_high and price < london_high:
            swept_high=True
    # 2. Break of Structure (BOS)
    # Last 20 candles swing high/low (excluding last 2)
    swing_high=float(df['High'].iloc[-22:-2].max()); swing_low=float(df['Low'].iloc[-22:-2].min())
    bos_up = price > swing_high
    bos_down = price < swing_low
    # 3. Fair Value Gap (FVG) - check last 5 candles for gap
    fvg_bull=False; fvg_bear=False; fvg_zone=""
    if len(df)>=4:
        # Bullish FVG: candle[-3] High < candle[-1] Low
        if float(df.iloc[-3]['High']) < float(df.iloc[-1]['Low']):
            fvg_bull=True; fvg_zone=f"{float(df.iloc[-3]['High']):.5f} - {float(df.iloc[-1]['Low']):.5f}"
        if float(df.iloc[-3]['Low']) > float(df.iloc[-1]['High']):
            fvg_bear=True; fvg_zone=f"{float(df.iloc[-1]['High']):.5f} - {float(df.iloc[-3]['Low']):.5f}"
    # 5M Confirm
    df5c=df5.iloc[:-1]; close5=df5c['Close']; r5=float(rsi(close5).iloc[-1]); m5,sig5=macd(close5); macd5=float(m5.iloc[-1]); sig5v=float(sig5.iloc[-1]); ema50_5=float(close5.ewm(span=50, adjust=False).mean().iloc[-1])
    trend_1h="UP" if price>ema200_1h else "DOWN"
    if adx_val<20: return None
    direction=None; m5_agree=False; smart_reason=[]
    if price>ema50 and 50<r<70 and macd_val>sig_val and pdi>mdi and trend_1h=="UP" and candle_up and ema20>ema50 and bos_up:
        direction="BUY"; m5_agree=(r5>50 and macd5>sig5v and price>ema50_5)
        if swept_low: smart_reason.append("💧 Liquidity Sweep of London Low")
        if fvg_bull: smart_reason.append(f"📦 Bullish FVG {fvg_zone}")
        smart_reason.append("📈 BOS - Broke Swing High")
    elif price<ema50 and 30<r<50 and macd_val<sig_val and mdi>pdi and trend_1h=="DOWN" and candle_down and ema20<ema50 and bos_down:
        direction="SELL"; m5_agree=(r5<50 and macd5<sig5v and price<ema50_5)
        if swept_high: smart_reason.append("💧 Liquidity Sweep of London High")
        if fvg_bear: smart_reason.append(f"📦 Bearish FVG {fvg_zone}")
        smart_reason.append("📉 BOS - Broke Swing Low")
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
    # BOS already required, give bonus
    score+=5
    score=min(99, max(55,int(score)))
    # V5.3: BLOCK B setups - Only A / A+
    if score < 80: return None
    grade="A+ SNIPER" if score>=90 else "A Setup"
    if name=="XAUUSD": sl_mult,tp1_mult,tp2_mult=1.2,1.8,3.5
    else: sl_mult,tp1_mult,tp2_mult=1.5,1.5,3.0
    if direction=="BUY": sl=price-atr*sl_mult; tp1=price+atr*tp1_mult; tp2=price+atr*tp2_mult
    else: sl=price+atr*sl_mult; tp1=price-atr*tp1_mult; tp2=price-atr*tp2_mult
    sl_dist=abs(price-sl)
    if "JPY" in name: sl_pips=sl_dist*100
    elif name=="XAUUSD": sl_pips=sl_dist*10
    else: sl_pips=sl_dist*10000
    lot=calc_lot(name, max(sl_pips,1))
    return {"pair":name,"dir":direction,"price":price,"rsi":r,"adx":adx_val,"trend1h":trend_1h,"score":score,"grade":grade,"sl":sl,"tp1":tp1,"tp2":tp2,"lot":lot,"sup":sup,"res":res,"london_high":london_high,"london_low":london_low,"m5_agree":m5_agree,"smart":smart_reason,"bos": "BOS Confirmed","sweep": swept_low or swept_high}

def in_session(now):
    wd=now.weekday()
    if wd>=5: return False
    if wd==0 and now.hour<12: return False
    if wd==4 and now.hour>=17: return False
    hm=now.hour*60+now.minute
    return (600<=hm<=840) or (900<=hm<=1140)

def get_live_price(ticker):
    try:
        df=yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        return float(df['Close'].iloc[-1])
    except: return None

def bot_loop():
    sent_today=set(); active={}; last_report=None; last_bias=None
    send("🎯 <b>V5.3 MANUAL PRO++ SMART MONEY is LIVE</b>\n✅ 3 Pairs: XAUUSD, EURUSD, GBPJPY\n✅ Liquidity Sweep Detection\n✅ Fair Value Gap (FVG)\n✅ Break of Structure (BOS)\n✅ ONLY A / A+ Signals - No B\n✅ 5M Confirm + London High/Low\n✅ GOLD BOOST\n✅ TP1 Alert\n💰 Balance: $%.0f\n🎯 SNIPER MODE - 1-2 Signals/Day, But Sharp" % ACCOUNT_BALANCE)
    while True:
        try:
            now=datetime.now(EAT)
            if now.hour==9 and now.minute<5 and last_bias!=now.date():
                last_bias=now.date(); lines=[]
                for n,t in PAIRS.items():
                    d=get_data(t,"1h","10d")
                    if d is not None:
                        c=d['Close']; e200=float(c.ewm(span=200, adjust=False).mean().iloc[-1]); p=float(c.iloc[-1])
                        lines.append(f"{n}: {'🟢 BUY ONLY' if p>e200 else '🔴 SELL ONLY'}")
                send(f"🌅 <b>TODAY'S BIAS - {now.strftime('%Y-%m-%d')}</b>\n"+"\n".join(lines)+"\n\nSNIPER MODE: Wait for A/A+ only. Max 2 trades.")
            if now.hour==21 and now.minute<5 and last_report!=now.date():
                last_report=now.date()
                send(f"📊 <b>DAILY REPORT - {now.strftime('%Y-%m-%d')}</b>\nSniper Signals Today: {len(sent_today)}\nBalance: ${ACCOUNT_BALANCE:.0f}\nBot: V5.3 SMART MONEY ✅")
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
                    send(f"⚠️ <b>TP1 HIT - MOVE SL NOW!</b>\n{pn} {tr['dir']}\nPrice hit TP1: {tr['tp1']:.5f}\n👉 Move SL to Entry NOW!\nThen TP to TP2: {tr['tp2']:.5f}\nThis trade is now FREE.")
            for name,ticker in PAIRS.items():
                key=f"{name}-{now.strftime('%Y-%m-%d-%H')}"
                if key in sent_today: continue
                res=analyze_pair(name,ticker)
                if res:
                    sent_today.add(key); active[name]={"dir":res['dir'],"tp1":res['tp1'],"tp2":res['tp2'],"sl":res['sl'],"alerted":False}
                    emoji="🟢" if res['dir']=="BUY" else "🔴"
                    m5_txt="✅ 5M CONFIRMED" if res['m5_agree'] else "⚠️ 5M Not Yet"
                    smart_txt="\n".join(res['smart']) if res['smart'] else "Smart Money: BOS Only"
                    msg=(f"{emoji} <b>{res['pair']} {res['dir']} - V5.3 SNIPER</b>\n"
                         f"⭐ Score: {res['score']}/100 - {res['grade']}\n"
                         f"🎯 <b>SMART MONEY REASON:</b>\n{smart_txt}\n"
                         f"{m5_txt} | BOS: ✅\n"
                         f"Price: {res['price']:.5f}\n"
                         f"RSI: {res['rsi']:.1f} | ADX: {res['adx']:.1f} | 1H: {res['trend1h']}\n"
                         f"Lot: {res['lot']} (Risk 1% = ${ACCOUNT_BALANCE*RISK_PCT:.2f})\n"
                         f"📊 London High: {res['london_high']:.5f}\n"
                         f"📊 London Low: {res['london_low']:.5f}\n"
                         f"🎯 TP1: {res['tp1']:.5f} (Move SL)\n"
                         f"🎯 TP2: {res['tp2']:.5f}\n"
                         f"🛑 SL: {res['sl']:.5f}\n"
                         f"⏰ {now.strftime('%H:%M')} EAT | ⏳ Valid 30 mins ONLY\n"
                         f"⚠️ SNIPER - Don't chase if late.")
                    send(msg)
                time.sleep(2)
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}"); time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__=="__main__":
    port=int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
