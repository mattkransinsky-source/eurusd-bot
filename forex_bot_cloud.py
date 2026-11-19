import os, time, requests, threading, pytz, gc, traceback, json, csv
from datetime import datetime as dt, timedelta
import yfinance as yf
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home(): return "V39 ALIVE", 200
@app.route('/health')
def health(): return "OK", 200
@app.route('/<path:path>')
def catch_all(path): return "V39 ALIVE", 200
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
TRADE_HISTORY_FILE = os.getenv("TRADE_FILE", "trade_history.json")
FEATURE_LOG = os.getenv("FEATURE_FILE", "feature_log.csv")
SYMBOLS = {"XAUUSD":"GC=F","GBPJPY":"GBPJPY=X","US30":"YM=F","GBPUSD":"GBPUSD=X","EURUSD":"EURUSD=X","EURJPY":"EURJPY=X","USDJPY":"USDJPY=X","AUDUSD":"AUDUSD=X","USDCAD":"USDCAD=X","NZDUSD":"NZDUSD=X","USDCHF":"USDCHF=X","EURGBP":"EURGBP=X"}
MAX_RISK = {"XAUUSD":12.0,"GBPJPY":0.8,"US30":120.0,"GBPUSD":0.005,"EURUSD":0.005,"EURJPY":0.8,"USDJPY":0.8,"AUDUSD":0.005,"USDCAD":0.005,"NZDUSD":0.005,"USDCHF":0.005,"EURGBP":0.005}
MIN_SL = {"XAUUSD":3.0,"GBPJPY":0.12,"US30":40.0,"GBPUSD":0.00080,"EURUSD":0.00080,"EURJPY":0.12,"USDJPY":0.12,"AUDUSD":0.00080,"USDCAD":0.00080,"NZDUSD":0.00080,"USDCHF":0.00080,"EURGBP":0.00080}
SPREAD = {"XAUUSD":0.30,"GBPJPY":0.03,"US30":5.0,"GBPUSD":0.00015,"EURUSD":0.00012,"EURJPY":0.03,"USDJPY":0.02,"AUDUSD":0.00015,"USDCAD":0.00015,"NZDUSD":0.00018,"USDCHF":0.00015,"EURGBP":0.00015}
SL_ATR_MULT = 1.5
SL_BUFFER = {"US30":20.0,"XAUUSD":2.0,"GBPJPY":0.08,"GBPUSD":0.00020,"EURUSD":0.00020,"EURJPY":0.08,"USDJPY":0.08,"AUDUSD":0.00020,"USDCAD":0.00020,"NZDUSD":0.00020,"USDCHF":0.00020,"EURGBP":0.00020}
YAHOO_PROXY_PAIRS = {"XAUUSD","US30"}
MAX_SIGNALS_PER_DAY = 999
MAX_SHADOW_PER_DAY = 999
COOLDOWN_MIN = 15
EXPOSURE = {"USD": ["EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD","USDCHF","USDJPY"], "JPY": ["GBPJPY","EURJPY","USDJPY"], "EUR": ["EURUSD","EURJPY","EURGBP"], "GBP": ["GBPUSD","GBPJPY","EURGBP"], "GOLD": ["XAUUSD"], "US30": ["US30"]}
CAT_CAPS = {"STRUCTURE":50,"TREND":25,"MOMENTUM":25,"CONTEXT":20}
PAIR_COOLDOWN_UNTIL = {}
PAIR_SIGNAL_COUNT = {}
PRICE_LEVEL_WINS = {}
PRICE_LEVEL_LOSSES = {}
USER_REPORTED_WINS = {}
USER_REPORTED_LOSSES = {}
PAIR_LAST_SIGNAL_TIME = {}
PAIR_LOSS_TODAY = {}
OUTCOME_LOG = []
PRICE_OUTCOMES = []
USER_OUTCOMES = []
ACTIVE_TRADES = []
PENDING_USER_REPORTS = []
VETO_LOG = []
SHADOW_TRADES = []
SHADOW_OUTCOMES = []
TELEGRAM_OFFSET = 0
LAST_PROCESSED_1M = {}
TRADE_COUNTER = 0
LAST_RESET_DATE = ""
COMPLETED_TRADE_IDS = set()
TOTAL_SIGNALS_TODAY = 0
TOTAL_SHADOW_TODAY = 0
OFFSETS = {"XAUUSD":0.0,"US30":0.0}
CACHE = {"4h":{},"cache_time":{}}
CACHE_LOCK = threading.Lock()
def save_outcomes():
    try:
        data = {"price_wins":PRICE_LEVEL_WINS,"price_losses":PRICE_LEVEL_LOSSES,"user_wins":USER_REPORTED_WINS,"user_losses":USER_REPORTED_LOSSES,"outcomes":OUTCOME_LOG[-200:],"veto_log":VETO_LOG[-200:],"price_outcomes":PRICE_OUTCOMES[-200:],"user_outcomes":USER_OUTCOMES[-200:],"active":ACTIVE_TRADES,"pending_reports":PENDING_USER_REPORTS,"shadow":SHADOW_TRADES,"shadow_outcomes":SHADOW_OUTCOMES[-200:],"offset":TELEGRAM_OFFSET,"last_processed":LAST_PROCESSED_1M,"trade_counter":TRADE_COUNTER,"completed_ids":list(COMPLETED_TRADE_IDS),"last_reset":LAST_RESET_DATE,"signal_count":PAIR_SIGNAL_COUNT,"cooldown":PAIR_COOLDOWN_UNTIL,"total_today":TOTAL_SIGNALS_TODAY,"shadow_today":TOTAL_SHADOW_TODAY,"offsets":OFFSETS,"pair_loss_today":PAIR_LOSS_TODAY}
        tmp = TRADE_HISTORY_FILE + ".tmp"
        with open(tmp,"w") as f: json.dump(data,f)
        os.replace(tmp, TRADE_HISTORY_FILE)
    except Exception as e: print(f"Save error {e}",flush=True)
def load_outcomes():
    global PRICE_LEVEL_WINS,PRICE_LEVEL_LOSSES,USER_REPORTED_WINS,USER_REPORTED_LOSSES,OUTCOME_LOG,PRICE_OUTCOMES,USER_OUTCOMES,VETO_LOG,ACTIVE_TRADES,PENDING_USER_REPORTS,SHADOW_TRADES,SHADOW_OUTCOMES,TELEGRAM_OFFSET,LAST_PROCESSED_1M,TRADE_COUNTER,COMPLETED_TRADE_IDS,LAST_RESET_DATE,PAIR_SIGNAL_COUNT,PAIR_COOLDOWN_UNTIL,TOTAL_SIGNALS_TODAY,TOTAL_SHADOW_TODAY,OFFSETS,PAIR_LOSS_TODAY
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE,"r") as f:
                d=json.load(f)
                PRICE_LEVEL_WINS=d.get("price_wins",{}); PRICE_LEVEL_LOSSES=d.get("price_losses",{})
                USER_REPORTED_WINS=d.get("user_wins",{}); USER_REPORTED_LOSSES=d.get("user_losses",{})
                OUTCOME_LOG=d.get("outcomes",[]); VETO_LOG=d.get("veto_log",[]); PRICE_OUTCOMES=d.get("price_outcomes",[]); USER_OUTCOMES=d.get("user_outcomes",[])
                ACTIVE_TRADES=d.get("active",[]); PENDING_USER_REPORTS=d.get("pending_reports",[]); SHADOW_TRADES=d.get("shadow",[]); SHADOW_OUTCOMES=d.get("shadow_outcomes",[])
                TELEGRAM_OFFSET=d.get("offset",0); LAST_PROCESSED_1M=d.get("last_processed",{}); TRADE_COUNTER=d.get("trade_counter",0)
                COMPLETED_TRADE_IDS=set(d.get("completed_ids",[])); LAST_RESET_DATE=d.get("last_reset",""); PAIR_SIGNAL_COUNT=d.get("signal_count",{}); PAIR_COOLDOWN_UNTIL=d.get("cooldown",{}); TOTAL_SIGNALS_TODAY=d.get("total_today",0); TOTAL_SHADOW_TODAY=d.get("shadow_today",0); OFFSETS.update(d.get("offsets",{})); PAIR_LOSS_TODAY=d.get("pair_loss_today",{})
    except Exception as e: print(f"Load error {e}",flush=True)
def log_features(row):
    try:
        exists = os.path.exists(FEATURE_LOG)
        with open(FEATURE_LOG,"a",newline="") as f:
            w=csv.DictWriter(f, fieldnames=list(row.keys()))
            if not exists: w.writeheader()
            w.writerow(row)
    except Exception as e: print(f"Feature log {e}",flush=True)
def send_telegram(msg, trade_id=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return False
    try:
        payload={"chat_id":TELEGRAM_CHAT_ID,"text":msg}
        if trade_id:
            payload["reply_markup"]={"inline_keyboard":[[{"text":"✅ CONFIRM","callback_data":f"CONFIRM {trade_id}"},{"text":"❌ SKIP","callback_data":f"SKIP {trade_id}"}],[{"text":"BE","callback_data":f"BE {trade_id}"},{"text":"WIN","callback_data":f"WIN {trade_id}"},{"text":"LOSS","callback_data":f"LOSS {trade_id}"}]]}
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",json=payload,timeout=10)
        try:
            j=r.json(); return r.status_code==200 and j.get("ok",False)
        except: return False
    except: return False
def answer_callback(cb_id, text=""):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",json={"callback_query_id":cb_id,"text":text},timeout=5)
    except: pass
def heartbeat():
    while True:
        print(f"HEARTBEAT {dt.now(pytz.timezone('Africa/Nairobi'))} Active {len(ACTIVE_TRADES)} Shadow {len(SHADOW_TRADES)} Signals {TOTAL_SIGNALS_TODAY}/999 Offsets {OFFSETS} PairLoss {PAIR_LOSS_TODAY}",flush=True); time.sleep(30)
def ema(values,period):
    k=2/(period+1); e=values[0]
    for v in values[1:]: e=v*k+e*(1-k)
    return e
def rsi_calc_wilder(values,period=14):
    if len(values)<period+1: return 50
    gains=[]; losses=[]
    for i in range(1,len(values)):
        diff=values[i]-values[i-1]; gains.append(max(diff,0)); losses.append(max(-diff,0))
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    for i in range(period,len(gains)):
        ag=(ag*(period-1)+gains[i])/period; al=(al*(period-1)+losses[i])/period
    if ag==0 and al==0: return 50
    if al==0: return 100
    rs=ag/al; return 100-(100/(1+rs))
def rsi_series_wilder(values,period=14):
    if len(values)<period+1: return [50]*len(values)
    out=[50]*len(values); gains=[]; losses=[]
    for i in range(1,len(values)):
        diff=values[i]-values[i-1]; gains.append(max(diff,0)); losses.append(max(-diff,0))
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    out[period]=100-(100/(1+(ag/al if al!=0 else 999))) if al!=0 else 100
    for i in range(period, len(gains)):
        ag=(ag*(period-1)+gains[i])/period; al=(al*(period-1)+losses[i])/period
        idx=i+1
        if idx<len(out): out[idx]=100-(100/(1+(ag/al if al!=0 else 999))) if al!=0 else 100
    return out
def rsi_divergence(closes, trend):
    try:
        if len(closes)<25: return 0, False
        rsi = rsi_series_wilder(closes,14)
        seg1 = closes[-25:-12]; seg2 = closes[-12:]
        p1 = max(seg1); p2 = max(seg2); i1 = seg1.index(p1); i2 = seg2.index(p2)
        r_at_p1 = rsi[-25+i1]; r_at_p2 = rsi[-12+i2]
        l1 = min(seg1); l2 = min(seg2); j1 = seg1.index(l1); j2 = seg2.index(l2)
        r_at_l1 = rsi[-25+j1]; r_at_l2 = rsi[-12+j2]
        if trend=="SELL":
            if p2 > p1 and r_at_p2 < r_at_p1 - 3: return 15, False
            if l2 < l1 and r_at_l2 > r_at_l1 + 3: return 0, True
        if trend=="BUY":
            if l2 < l1 and r_at_l2 > r_at_l1 + 3: return 15, False
            if p2 > p1 and r_at_p2 < r_at_p1 - 3: return 0, True
        return 0, False
    except: return 0, False
def vwap_calc_daily(df):
    try:
        if df is None or len(df)==0: return 0
        last_day = df.index[-1].date()
        day_df = df[df.index.date == last_day] if hasattr(df.index[-1],'date') else df.tail(50)
        if len(day_df)==0: day_df=df.tail(50)
        tp = (day_df['High']+day_df['Low']+day_df['Close'])/3
        vol = day_df['Volume'] if 'Volume' in day_df.columns else None
        if vol is None: return float(df['Close'].iloc[-1])
        vol = vol.fillna(0)
        if vol.sum()==0: return float(df['Close'].iloc[-1])
        cum = (tp*vol).cumsum() / vol.cumsum()
        val = float(cum.iloc[-1])
        if val != val: return float(df['Close'].iloc[-1])
        return val
    except: return float(df['Close'].iloc[-1]) if df is not None and len(df)>0 else 0
def atr_calc_wilder(highs,lows,closes,period=14):
    if len(closes)<period+1: return abs(closes[-1]*0.001)
    trs=[]
    for i in range(1,len(closes)):
        tr=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])); trs.append(tr)
    atr=sum(trs[:period])/period
    for tr in trs[period:]: atr=(atr*(period-1)+tr)/period
    return atr
def adx_calc_true_wilder(highs,lows,closes,period=14):
    try:
        if len(closes)<period*3+1: return 20,0,0
        trs=[]; plus_dm=[]; minus_dm=[]
        for i in range(1,len(closes)):
            tr=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])); trs.append(tr)
            up=highs[i]-highs[i-1]; down=lows[i-1]-lows[i]
            plus_dm.append(up if (up>down and up>0) else 0); minus_dm.append(down if (down>up and down>0) else 0)
        atr=sum(trs[:period]); p_dm=sum(plus_dm[:period]); m_dm=sum(minus_dm[:period])
        dx_series=[]
        if atr!=0:
            pdi0=100*p_dm/atr; mdi0=100*m_dm/atr; denom0=pdi0+mdi0
            dx_series.append(100*abs(pdi0-mdi0)/denom0 if denom0!=0 else 0)
        for i in range(period,len(trs)):
            atr=atr-(atr/period)+trs[i]; p_dm=p_dm-(p_dm/period)+plus_dm[i]; m_dm=m_dm-(m_dm/period)+minus_dm[i]
            if atr==0: continue
            pdi=100*p_dm/atr; mdi=100*m_dm/atr; denom=pdi+mdi
            dx_series.append(100*abs(pdi-mdi)/denom if denom!=0 else 0)
        if len(dx_series)<period: return round(float(dx_series[-1]) if dx_series else 20,1),0,0
        adx=sum(dx_series[:period])/period
        for dx in dx_series[period:]: adx=(adx*(period-1)+dx)/period
        if atr==0: return round(float(adx),1),0,0
        return round(float(adx),1),round(float(100*p_dm/atr),1),round(float(100*m_dm/atr),1)
    except: return 20,0,0
def get_data(sym,interval,period):
    try:
        df=yf.download(sym,interval=interval,period=period,progress=False,auto_adjust=False,threads=False,timeout=20)
        if df.empty: return None
        if hasattr(df.columns,'nlevels') and df.columns.nlevels>1: df.columns=df.columns.get_level_values(0)
        return df
    except: return None
def get_data_cached(sym,interval,period,cache_key=None):
    if cache_key:
        with CACHE_LOCK:
            ct=CACHE["cache_time"].get(cache_key,0)
            if time.time()-ct < 3600 and cache_key in CACHE.get("4h",{}): return CACHE["4h"][cache_key]
        df=get_data(sym,interval,period)
        with CACHE_LOCK:
            CACHE.setdefault("4h",{})[cache_key]=df; CACHE["cache_time"][cache_key]=time.time()
        return df
    return get_data(sym,interval,period)
def get_4h_from_1h(ysym):
    try:
        h1=get_data_cached(ysym,"1h","10d",cache_key=f"{ysym}_1h_for_4h")
        if h1 is None or len(h1)==0: return None
        df=h1.resample("4h").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        return df
    except: return get_data(ysym,"4h","60d")
def get_fresh_price(ysym):
    try:
        df=get_data(ysym,"1m","1d")
        if df is None or len(df)==0: return None
        if len(df)>1: return float(df['Close'].iloc[-2])
        return float(df['Close'].iloc[-1])
    except: return None
def find_obs(df_5m,trend):
    highs=df_5m['High'].tolist(); lows=df_5m['Low'].tolist(); closes=df_5m['Close'].tolist(); opens=df_5m['Open'].tolist(); obs=[]; n=len(df_5m)
    for i in range(max(5,n-30),n-3):
        if closes[i]>max(highs[i-5:i]):
            for j in range(i-1,max(i-5,0),-1):
                if closes[j]<opens[j]:
                    top,bottom=highs[j],lows[j]; broken=False
                    for k in range(j+1,n):
                        if closes[k]<bottom: broken=True; break
                    if broken and trend=="SELL": obs.append({"type":"BREAKER_BEAR","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="BUY":
                        taps=sum(1 for k in range(j+1,n) if bottom<=lows[k]<=top); t="MITIGATION_BULL" if taps>=1 else "BULL_OB"; obs.append({"type":t,"top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    break
        if closes[i]<min(lows[i-5:i]):
            for j in range(i-1,max(i-5,0),-1):
                if closes[j]>opens[j]:
                    top,bottom=highs[j],lows[j]; broken=False
                    for k in range(j+1,n):
                        if closes[k]>top: broken=True; break
                    if broken and trend=="BUY": obs.append({"type":"BREAKER_BULL","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="SELL":
                        taps=sum(1 for k in range(j+1,n) if bottom<=highs[k]<=top); t="MITIGATION_BEAR" if taps>=1 else "BEAR_OB"; obs.append({"type":t,"top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    break
    return obs
def is_high_impact_now(now_utc):
    try:
        if now_utc.weekday()==4 and now_utc.day<=7:
            if 12.0 <= now_utc.hour+now_utc.minute/60.0 <= 13.0: return True, "NFP"
        if 12.0 <= now_utc.hour+now_utc.minute/60.0 <= 13.0:
            if now_utc.weekday() in [2,4]: return True, "US Data 12:30 UTC"
        if now_utc.weekday()==3 and 11.0 <= now_utc.hour+now_utc.minute/60.0 <= 12.0: return True, "ECB/BoE"
        return False, ""
    except: return False, ""
def is_news_time(now_nairobi):
    now_utc = now_nairobi.astimezone(pytz.UTC)
    blocked, label = is_high_impact_now(now_utc)
    return blocked
def get_session_label(now_nairobi):
    try:
        london = now_nairobi.astimezone(pytz.timezone("Europe/London"))
        ny = now_nairobi.astimezone(pytz.timezone("America/New_York"))
        lh = london.hour + london.minute/60.0; nh = ny.hour + ny.minute/60.0
        if 8.0 <= lh < 11.0: return "LONDON PRIME", True
        if 8.0 <= nh < 12.0: return "NY PRIME", True
        if 11.0 <= lh < 13.0: return "CHOP TIME", False
        if 12.0 <= nh < 14.0: return "LATE - Careful", False
        return "OFF", False
    except:
        hour=now_nairobi.hour+now_nairobi.minute/60.0
        if 9.0<=hour<12.0: return "LONDON PRIME",True
        if 15.0<=hour<19.0: return "NY PRIME",True
        return "OFF",False
def get_asia_range(df_5m):
    try:
        if df_5m is None or len(df_5m)==0: return None, None
        idx = df_5m.index.tz_convert("UTC") if hasattr(df_5m.index,'tz_convert') and df_5m.index.tz is not None else df_5m.index
        mask = [(t.hour>=0 and t.hour<7) for t in idx]
        asia = df_5m[mask]
        if len(asia)==0: return None, None
        last_date = idx[mask][-1].date() if any(mask) else None
        asia_last = asia[[t.date()==last_date for t in idx[mask]]] if last_date else asia
        return float(asia_last['High'].max()), float(asia_last['Low'].min())
    except: return None, None
def maybe_reset_daily(now):
    global PAIR_SIGNAL_COUNT,LAST_RESET_DATE,TOTAL_SIGNALS_TODAY,TOTAL_SHADOW_TODAY,PAIR_LOSS_TODAY
    today=now.strftime("%Y-%m-%d")
    if LAST_RESET_DATE!=today:
        PAIR_SIGNAL_COUNT={}; PAIR_LAST_SIGNAL_TIME.clear(); LAST_RESET_DATE=today; TOTAL_SIGNALS_TODAY=0; TOTAL_SHADOW_TODAY=0; PAIR_LOSS_TODAY={}; save_outcomes()
def new_trade_id():
    global TRADE_COUNTER; TRADE_COUNTER+=1; save_outcomes(); return f"T{TRADE_COUNTER:03d}"
def new_shadow_id():
    global TRADE_COUNTER; TRADE_COUNTER+=1; save_outcomes(); return f"S{TRADE_COUNTER:03d}"
def build_pending_copy(trade,auto_result):
    t=dict(trade); t["auto_result"]=auto_result; t["auto_close_time"]=time.time(); t["status"]="PENDING_USER_REPORT"; return t
def cleanup_pending_reports():
    global PENDING_USER_REPORTS
    now_t=time.time(); new=[]
    for p in PENDING_USER_REPORTS:
        if now_t-p.get("auto_close_time",now_t)<24*3600: new.append(p)
        else:
            if p.get("trade_id"): COMPLETED_TRADE_IDS.add(p.get("trade_id"))
    if len(new)!=len(PENDING_USER_REPORTS): PENDING_USER_REPORTS=new; save_outcomes()
def handle_command(cmd, trade_id, from_id, chat_id, cb_id=None):
    if TELEGRAM_USER_ID and str(from_id) != str(TELEGRAM_USER_ID):
        if cb_id: answer_callback(cb_id, "Not authorized")
        else: send_telegram("⛔ Not authorized")
        print(f"SECURITY BLOCK: from={from_id} tried {cmd} {trade_id}", flush=True)
        return
    def find_trade():
        if not trade_id: return None,None
        for t in ACTIVE_TRADES:
            if t.get("trade_id")==trade_id: return t,"ACTIVE"
        for t in PENDING_USER_REPORTS:
            if t.get("trade_id")==trade_id: return t,"PENDING_REPORT"
        return None,None
    if cmd=="CONFIRM":
        if not trade_id: send_telegram("Need ID. Use: CONFIRM T001"); return
        t,where=find_trade()
        if not t: send_telegram(f"CONFIRM FAILED - No active {trade_id}"); return
        if where=="PENDING_REPORT": send_telegram(f"CONFIRM FAILED - {trade_id} closed. Use WIN/LOSS."); return
        if t.get("status")!="PENDING_CONFIRM": send_telegram(f"Already confirmed - {trade_id}"); return
        t["status"]="PENDING_FILL"; t["confirm_time"]=time.time(); t["confirm_ts"]=time.time(); t["last_processed"]=time.time()
        fresh=get_fresh_price(SYMBOLS.get(t["pair"]))
        t["confirm_price"]=fresh if fresh is not None else t.get("live_price", t["entry"])
        t["confirm_price_time"]=time.time()
        LAST_PROCESSED_1M[t["pair"]]=time.time()
        send_telegram(f"✅ CONFIRMED {trade_id} {t['pair']} {t['trend']}\nEntry {t['entry']} | Now {t['confirm_price']:.5f}")
        if cb_id: answer_callback(cb_id, "Confirmed")
        save_outcomes()
    elif cmd=="BE":
        if not trade_id: send_telegram("Need ID"); return
        t,where=find_trade()
        if not t: send_telegram(f"BE FAILED - No {trade_id}"); return
        if t.get("be_done"): send_telegram(f"BE already done - {trade_id}"); return
        t["be_done"]=True; send_telegram(f"✅ BE DONE {trade_id}"); save_outcomes()
        if cb_id: answer_callback(cb_id, "BE done")
    elif cmd=="WIN":
        if not trade_id: send_telegram("Need ID"); return
        if trade_id in COMPLETED_TRADE_IDS: send_telegram(f"Duplicate WIN - {trade_id}"); return
        t,where=find_trade()
        if not t: send_telegram(f"WIN FAILED - No {trade_id}"); return
        pair=t["pair"]; tid=t.get("trade_id")
        USER_REPORTED_WINS[pair]=USER_REPORTED_WINS.get(pair,0)+1
        if tid: COMPLETED_TRADE_IDS.add(tid)
        ACTIVE_TRADES[:] = [x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]; PENDING_USER_REPORTS[:] = [x for x in PENDING_USER_REPORTS if x.get("trade_id")!=tid]
        USER_OUTCOMES.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER WIN","r":None,"source":"BROKER"})
        OUTCOME_LOG.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER-REPORTED WIN","r":None})
        send_telegram(f"✅ WIN {tid} {pair}"); save_outcomes()
        if cb_id: answer_callback(cb_id, "WIN recorded")
    elif cmd=="LOSS":
        if not trade_id: send_telegram("Need ID"); return
        if trade_id in COMPLETED_TRADE_IDS: send_telegram(f"Duplicate LOSS - {trade_id}"); return
        t,where=find_trade()
        if not t: send_telegram(f"LOSS FAILED - No {trade_id}"); return
        pair=t["pair"]; tid=t.get("trade_id")
        USER_REPORTED_LOSSES[pair]=USER_REPORTED_LOSSES.get(pair,0)+1
        PAIR_LOSS_TODAY[pair]=PAIR_LOSS_TODAY.get(pair,0)+1
        if tid: COMPLETED_TRADE_IDS.add(tid)
        ACTIVE_TRADES[:] = [x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]; PENDING_USER_REPORTS[:] = [x for x in PENDING_USER_REPORTS if x.get("trade_id")!=tid]
        USER_OUTCOMES.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER LOSS","r":None,"source":"BROKER"})
        OUTCOME_LOG.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER-REPORTED LOSS","r":None})
        send_telegram(f"❌ LOSS {tid} {pair} - Paused 2h, next signal needs 0 flags"); PAIR_COOLDOWN_UNTIL[pair]=time.time()+7200; save_outcomes()
        if cb_id: answer_callback(cb_id, "LOSS recorded")
    elif cmd=="SKIP":
        if not trade_id: return
        t,where=find_trade()
        if t and t.get("status")=="PENDING_CONFIRM":
            ACTIVE_TRADES[:] = [x for x in ACTIVE_TRADES if x.get("trade_id")!=trade_id]
            send_telegram(f"⏭️ SKIPPED {trade_id}"); save_outcomes()
        if cb_id: answer_callback(cb_id, "Skipped")
def poll_telegram_loop():
    global TELEGRAM_OFFSET
    while True:
        try:
            if not TELEGRAM_TOKEN: time.sleep(5); continue
            r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params={"offset":TELEGRAM_OFFSET,"timeout":5},timeout=10)
            if r.status_code!=200: time.sleep(3); continue
            data=r.json()
            for upd in data.get("result",[]):
                TELEGRAM_OFFSET=upd["update_id"]+1
                if "callback_query" in upd:
                    cb=upd["callback_query"]; cb_id=cb.get("id",""); from_id=str(cb.get("from",{}).get("id","")); chat_id=str(cb.get("message",{}).get("chat",{}).get("id",""))
                    if TELEGRAM_CHAT_ID and chat_id!=str(TELEGRAM_CHAT_ID): continue
                    txt=(cb.get("data") or "").strip().upper()
                    parts=txt.split(); cmd=parts[0] if parts else ""; tid=None
                    for p in parts[1:]:
                        if p.startswith("T") and p[1:].isdigit(): tid=p; break
                    handle_command(cmd, tid, from_id, chat_id, cb_id=cb_id)
                    continue
                msg=upd.get("message",{}); chat_id=str(msg.get("chat",{}).get("id","")); from_id=str(msg.get("from",{}).get("id","")); text=(msg.get("text") or "").strip()
                if not text: continue
                if TELEGRAM_CHAT_ID and chat_id!=str(TELEGRAM_CHAT_ID): continue
                up=text.upper(); parts=up.split(); cmd=parts[0]; tid=None
                for p in parts[1:]:
                    if p.startswith("T") and len(p)>=4 and p[1:].isdigit(): tid=p; break
                if cmd=="PING":
                    if TELEGRAM_USER_ID and from_id!=str(TELEGRAM_USER_ID): continue
                    send_telegram("✅ PONG - V39 24/5")
                elif cmd=="RESET_OFFSET":
                    if TELEGRAM_USER_ID and from_id!=str(TELEGRAM_USER_ID): continue
                    TELEGRAM_OFFSET=0; send_telegram("✅ Offset reset")
                elif cmd=="OFFSET":
                    if TELEGRAM_USER_ID and from_id!=str(TELEGRAM_USER_ID): continue
                    try:
                        pair=parts[1]; val=float(parts[2]); OFFSETS[pair]=val; save_outcomes(); send_telegram(f"✅ OFFSET {pair} = {val}")
                    except: send_telegram("Use: OFFSET XAUUSD 7.5")
                elif cmd=="STATUS":
                    if TELEGRAM_USER_ID and from_id!=str(TELEGRAM_USER_ID): continue
                    last = "\n".join([f"{v.get('pair')} {v.get('reason')}" for v in VETO_LOG[-3:]]) if VETO_LOG else "None"
                    txt=f"V39 STATUS 24/5\nSignals {TOTAL_SIGNALS_TODAY}/999 | Shadow {TOTAL_SHADOW_TODAY}/999\nActive {len(ACTIVE_TRADES)} | Shadow active {len(SHADOW_TRADES)}\nOffsets {OFFSETS}\nPair loss today {PAIR_LOSS_TODAY}\nLast hard blocks:\n{last}"
                    send_telegram(txt)
                elif cmd=="STATS":
                    if TELEGRAM_USER_ID and from_id!=str(TELEGRAM_USER_ID): continue
                    wins=sum(USER_REPORTED_WINS.values()); losses=sum(USER_REPORTED_LOSSES.values()); total=wins+losses
                    wr=round(wins/total*100,1) if total>0 else 0
                    rs=[o.get("r") for o in PRICE_OUTCOMES if o.get("r") is not None]
                    avg_r=round(sum(rs)/len(rs),2) if rs else 0
                    txt=f"📊 V39 STATS\nUser: {wins}W / {losses}L = {wr}% WR\nPrice Avg R {avg_r}\nNeed ~45-50% to break even\n\nSHADOW:\n"
                    from collections import defaultdict
                    by_flag=defaultdict(list)
                    for s in SHADOW_OUTCOMES:
                        if s.get("r")==0: continue
                        if not s.get("flags"): by_flag["CLEAN"].append(s.get("r",0))
                        else:
                            for f in s.get("flags",[]): by_flag[f].append(s.get("r",0))
                    for flag, rlist in by_flag.items():
                        if not rlist: continue
                        w=sum(1 for r in rlist if r and r>0); l=sum(1 for r in rlist if r and r<0); tot=w+l
                        wr2=round(w/tot*100,1) if tot>0 else 0
                        avg=round(sum([r for r in rlist if r is not None])/len(rlist),2) if rlist else 0
                        txt+=f"{flag}: {w}W {l}L = {wr2}% | Avg R {avg} | n={tot}\n"
                    if not SHADOW_OUTCOMES: txt+="No shadow yet - need 1-2 weeks"
                    txt+="\nIf CHOP shadow WR = Clean WR -> drop CHOP. If 1H_CONFLICT shadow WR << Clean -> make it hard block."
                    send_telegram(txt)
                elif cmd in ["CONFIRM","BE","WIN","LOSS","SKIP"]:
                    handle_command(cmd, tid, from_id, chat_id)
            save_outcomes()
        except Exception as e: print(f"Poll error {e}",flush=True)
        time.sleep(3)
def check_active_trades_loop():
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz)
            cleanup_pending_reports(); check_active_trades(now); check_shadow_trades(now)
        except Exception as e: print(f"Track error {e}",flush=True)
        time.sleep(10)
def _almost_tp_thresholds(pair):
    if pair=="XAUUSD": return 0.30, 0.40
    if pair=="US30": return 3.0, 4.0
    if pair in ("GBPJPY","EURJPY","USDJPY"): return 0.03, 0.04
    return 0.00030, 0.00040
def check_shadow_trades(now):
    global SHADOW_TRADES, SHADOW_OUTCOMES
    if not SHADOW_TRADES: return
    remaining=[]
    for t in SHADOW_TRADES:
        try:
            ysym=SYMBOLS.get(t["pair"])
            if not ysym: continue
            if "tp" not in t and "tp1" in t: t["tp"]=t["tp1"]
            tp=t.get("tp", t.get("tp1"))
            if t.get("status")=="SHADOW_PENDING":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(t); continue
                df_c=df.iloc[:-1] if len(df)>1 else df
                last_close=float(df_c['Close'].iloc[-1])
                if t["trend"]=="BUY" and last_close>=tp:
                    SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW STALE - TP before fill","r":0}); continue
                if t["trend"]=="SELL" and last_close<=tp:
                    SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW STALE","r":0}); continue
                touched=False
                for idx,row in df_c.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts < t["time"]-60: continue
                    low=float(row['Low']); high=float(row['High'])
                    if t["trend"]=="BUY" and low<=t["entry"]: touched=True; break
                    if t["trend"]=="SELL" and high>=t["entry"]: touched=True; break
                if touched:
                    t["status"]="SHADOW_ACTIVE"; t["touch_time"]=time.time(); t["last_processed"]=time.time(); remaining.append(t)
                else:
                    if time.time()-t["time"]>600:
                        SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW EXPIRED No Touch","r":0})
                    else: remaining.append(t)
                continue
            if t.get("status")=="SHADOW_ACTIVE":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(t); continue
                df_c=df.iloc[:-1] if len(df)>1 else df
                last_ts=t.get("last_processed",t["time"]); done=False
                for idx,row in df_c.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts<=last_ts: continue
                    high=float(row['High']); low=float(row['Low'])
                    entry=t["entry"]; sl=t["sl"]
                    if t["trend"]=="BUY":
                        if high>=tp:
                            SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW TP 0.7R","r":0.7}); done=True; break
                        if low<=sl:
                            SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW SL","r":-1.0}); done=True; break
                    else:
                        if low<=tp:
                            SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW TP 0.7R","r":0.7}); done=True; break
                        if high>=sl:
                            SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW SL","r":-1.0}); done=True; break
                    t["last_processed"]=ts
                if not done:
                    if time.time()-t.get("touch_time",t["time"])>3600:
                        SHADOW_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":t["pair"],"trade_id":t.get("shadow_id"),"flags":t.get("flags",[]),"result":"SHADOW TIMEOUT","r":0})
                    else: remaining.append(t)
                continue
        except Exception as e: print(f"Shadow error {e}",flush=True); remaining.append(t)
    SHADOW_TRADES=remaining; save_outcomes()
def check_active_trades(now):
    global ACTIVE_TRADES,PENDING_USER_REPORTS
    if not ACTIVE_TRADES: return
    remaining=[]; new_pending=list(PENDING_USER_REPORTS)
    def atomic_move(trade,auto_result):
        copy=build_pending_copy(trade,auto_result)
        if not any(p.get("trade_id")==copy.get("trade_id") for p in new_pending): new_pending.append(copy)
        return copy
    for trade in ACTIVE_TRADES:
        try:
            name=trade["pair"]; ysym=SYMBOLS.get(name)
            if not ysym: continue
            if "tp" not in trade and "tp1" in trade: trade["tp"]=trade["tp1"]
            status=trade.get("status","PENDING_CONFIRM")
            if status=="PENDING_CONFIRM":
                age=(time.time()-trade["time"])/60
                # V39 FIX: Don't STALE-cancel LIMIT orders - entry is MEANT to be away from price
                is_limit = "LIMIT" in str(trade.get("order_type",""))
                try:
                    fresh=get_fresh_price(ysym)
                    if fresh is not None and not is_limit:
                        atr_v=trade.get("atr",0)
                        if atr_v>0 and abs(fresh-trade["entry"])>atr_v*1.0:
                            send_telegram(f"❌ STALE {name} {trade.get('trade_id')} Price moved >1 ATR from entry - Cancelled")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"STALE - >1 ATR","entry":trade["entry"]}); atomic_move(trade,"STALE - >1 ATR"); continue
                        if trade["trend"]=="BUY" and fresh>=trade["tp"]:
                            send_telegram(f"❌ STALE {name} {trade.get('trade_id')} TP hit before fill - Cancelled")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"STALE - TP before fill","entry":trade["entry"]}); atomic_move(trade,"STALE - TP before fill"); continue
                        if trade["trend"]=="SELL" and fresh<=trade["tp"]:
                            send_telegram(f"❌ STALE {name} {trade.get('trade_id')} TP hit before fill - Cancelled")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"STALE - TP before fill","entry":trade["entry"]}); atomic_move(trade,"STALE - TP before fill"); continue
                except: pass
                if age>10:
                    atomic_move(trade,"EXPIRED - No Confirm"); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"EXPIRED - No Confirm","entry":trade["entry"]})
                else: remaining.append(trade)
                continue
            if status=="PENDING_FILL":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                confirm_ts=trade.get("confirm_ts",trade.get("confirm_time",trade["time"])); window_end=confirm_ts+600; touched=False; touch_row=None; touch_idx=None
                confirm_price=trade.get("confirm_price", trade.get("live_price", trade["entry"]))
                for idx,row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts<confirm_ts: continue
                    if ts>window_end: continue
                    low=float(row['Low']); high=float(row['High']); close=float(row['Close'])
                    if trade["trend"]=="BUY":
                        if confirm_price <= trade["entry"]: continue
                        if low<=trade["entry"] and close>trade["sl"]: touched=True; touch_row=row; touch_idx=idx; break
                    else:
                        if confirm_price >= trade["entry"]: continue
                        if high>=trade["entry"] and close<trade["sl"]: touched=True; touch_row=row; touch_idx=idx; break
                if touched:
                    if touch_row is not None:
                        high_e=float(touch_row['High']); low_e=float(touch_row['Low']); entry_v=trade["entry"]; sl_v=trade["sl"]; tp_v=trade["tp"]
                        if trade["trend"]=="BUY": same=(low_e<=sl_v) or (high_e>=tp_v)
                        else: same=(high_e>=sl_v) or (low_e<=tp_v)
                        if same:
                            send_telegram(f"⚠️ SAME CANDLE - {name} {trade.get('trade_id')} Check broker"); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"SAME CANDLE UNVERIFIED","entry":entry_v}); atomic_move(trade,"SAME CANDLE UNVERIFIED"); continue
                    trade["status"]="PRICE_TOUCHED"; trade["touch_time"]=time.time(); trade["entry_candle_ts"]=touch_idx.timestamp() if hasattr(touch_idx,'timestamp') else time.time(); trade["last_processed"]=trade["entry_candle_ts"]; trade["best_favorable"]=trade["entry"]; trade["almost_tp_alerted"]=False; LAST_PROCESSED_1M[name]=trade["last_processed"]
                    send_telegram(f"🔔 TOUCHED {name} {trade.get('trade_id')} Entry {trade['entry']}"); remaining.append(trade)
                else:
                    confirm_t=trade.get("confirm_time",trade["time"]); age=(time.time()-confirm_t)/60
                    if len(df_completed)>0: trade["last_processed"]=df_completed.index[-1].timestamp() if hasattr(df_completed.index[-1],'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]
                    if age>10:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"EXPIRED - No Touch 10m","entry":trade["entry"]}); atomic_move(trade,"EXPIRED - No Touch 10m")
                    else: remaining.append(trade)
                continue
            if status=="PRICE_TOUCHED":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                last_ts=trade.get("last_processed",trade.get("confirm_ts",time.time()-3600)); candles=[]
                for idx,row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts>last_ts: candles.append((idx,row))
                if not candles:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20:
                        send_telegram(f"⏰ TIMEOUT {name} {trade.get('trade_id')} Close or BE - Idea dead, don't hold 2h"); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TIMEOUT 20m","entry":trade["entry"]}); atomic_move(trade,"TIMEOUT 20m")
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"WATCH EXPIRED","entry":trade["entry"]}); atomic_move(trade,"WATCH EXPIRED")
                    else: remaining.append(trade)
                    continue
                trade_done=False
                for idx,row in candles:
                    if trade_done: break
                    high=float(row['High']); low=float(row['Low']); close=float(row['Close'])
                    entry=trade["entry"]; sl=trade["sl"]; tp=trade["tp"]; trend=trade["trend"]
                    be_done=trade.get("be_done", False); effective_sl=entry if be_done else sl
                    if trend=="BUY":
                        trade["best_favorable"]=max(trade.get("best_favorable",entry), high)
                    else:
                        trade["best_favorable"]=min(trade.get("best_favorable",entry), low)
                    if not trade.get("almost_tp_alerted", False):
                        near_d, pull_d = _almost_tp_thresholds(name)
                        if trend=="BUY":
                            dist_to_tp = tp - trade["best_favorable"]
                            pullback = trade["best_favorable"] - close
                            if dist_to_tp <= near_d and pullback >= pull_d:
                                trade["almost_tp_alerted"]=True
                                send_telegram(f"⚠️ CLOSE NOW - TP Rejected {name} {trade.get('trade_id')}\nPrice got within 3 pips of TP {tp} then dropped 4 pips. Close NOW, don't wait for SL.")
                        else:
                            dist_to_tp = trade["best_favorable"] - tp
                            pullback = close - trade["best_favorable"]
                            if dist_to_tp <= near_d and pullback >= pull_d:
                                trade["almost_tp_alerted"]=True
                                send_telegram(f"⚠️ CLOSE NOW - TP Rejected {name} {trade.get('trade_id')}\nPrice got within 3 pips of TP {tp} then dropped 4 pips. Close NOW, don't wait for SL.")
                    if trend=="BUY": tp_t=high>=tp; sl_t=low<=effective_sl
                    else: tp_t=low<=tp; sl_t=high>=effective_sl
                    if tp_t and sl_t:
                        send_telegram(f"⚠️ TP+SL SAME CANDLE {name} {trade.get('trade_id')} Check broker"); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP+SL UNVERIFIED","entry":entry}); atomic_move(trade,"TP+SL UNVERIFIED"); trade_done=True; break
                    if tp_t:
                        send_telegram(f"🎉 TP HIT {name} {trade.get('trade_id')} Close FULL - Reply WIN"); PRICE_LEVEL_WINS[name]=PRICE_LEVEL_WINS.get(name,0)+1; PRICE_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"PRICE TP 0.7R","r":0.7,"source":"YAHOO"}); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP +0.7R","entry":entry,"r":0.7}); atomic_move(trade,"TP +0.7R"); trade_done=True; break
                    if sl_t:
                        send_telegram(f"❌ SL HIT {name} {trade.get('trade_id')} Reply LOSS"); PRICE_LEVEL_LOSSES[name]=PRICE_LEVEL_LOSSES.get(name,0)+1; PAIR_COOLDOWN_UNTIL[name]=time.time()+7200; PRICE_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"PRICE SL","r":-1.0,"source":"YAHOO"}); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"SL -1R","entry":entry,"r":-1.0}); atomic_move(trade,"SL -1R"); trade_done=True; break
                    trade["last_processed"]=idx.timestamp() if hasattr(idx,'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]
                if not trade_done:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20:
                        send_telegram(f"⏰ TIMEOUT {name} {trade.get('trade_id')} Close or BE - Idea dead"); OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TIMEOUT","entry":trade["entry"]}); atomic_move(trade,"TIMEOUT")
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"WATCH EXPIRED","entry":trade["entry"]}); atomic_move(trade,"WATCH EXPIRED")
                    else: remaining.append(trade)
        except Exception as e: print(f"Check error {e}",flush=True); remaining.append(trade)
    ACTIVE_TRADES[:]=remaining; PENDING_USER_REPORTS[:]=new_pending; save_outcomes()
def analyze(name,ysym,now,dfs=None):
    if dfs is None:
        df_4h=get_4h_from_1h(ysym); df_1h=get_data(ysym,"1h","10d"); df_15m=get_data(ysym,"15m","10d"); df_5m=get_data(ysym,"5m","2d"); df_1m=get_data(ysym,"1m","1d"); df_1d=get_data_cached(ysym,"1d","60d",cache_key=f"{ysym}_1d"); df_1w=get_data_cached(ysym,"1wk","6mo",cache_key=f"{ysym}_1w")
    else:
        df_4h=dfs.get("4h"); df_1h=dfs.get("1h"); df_15m=dfs.get("15m"); df_5m=dfs.get("5m"); df_1m=dfs.get("1m"); df_1d=dfs.get("1d"); df_1w=dfs.get("1w")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None: return {"fail":True,"hard":True,"score":0,"reason":"No data","flags":["NO_DATA"]}
    if len(df_5m)<50 or len(df_15m)<50 or len(df_1h)<20 or len(df_4h)<20: return {"fail":True,"hard":True,"score":0,"reason":"Not enough candles","flags":["NO_DATA"]}
    df_4h=df_4h.iloc[:-1]; df_1h=df_1h.iloc[:-1]; df_15m=df_15m.iloc[:-1]; df_5m=df_5m.iloc[:-1]
    if df_1m is not None and len(df_1m)>1: df_1m=df_1m.iloc[:-1]
    elif df_1m is not None and len(df_1m)==1: df_1m=df_1m.iloc[0:0]
    c15=df_15m['Close'].tolist(); c5=df_5m['Close'].tolist(); c1=df_1h['Close'].tolist(); c4=df_4h['Close'].tolist()
    h5=df_5m['High'].tolist(); l5=df_5m['Low'].tolist(); h15=df_15m['High'].tolist(); l15=df_15m['Low'].tolist()
    v15=df_15m['Volume'].tolist() if 'Volume' in df_15m.columns else None
    flags=[]; details=[]; warnings=[]; tags=[]
    cat={"STRUCTURE":0,"TREND":0,"MOMENTUM":0,"CONTEXT":0}
    def add(cat_name, pts, msg):
        cat[cat_name]+=pts; details.append(msg)
    e50_4h=ema(c4,50); e20_4h=ema(c4,20); p4=c4[-1]
    htf4="BUY" if p4>e50_4h and p4>e20_4h else ("SELL" if p4<e50_4h and p4<e20_4h else None)
    e50_1h=ema(c1,50); e20_1h=ema(c1,20); p1=c1[-1]
    htf="BUY" if p1>e50_1h and p1>e20_1h else ("SELL" if p1<e50_1h and p1<e20_1h else None)
    e50=ema(c15,50); e200=ema(c15,200); price=c15[-1]; trend=None
    if price>e50 and price>e200: trend="BUY"; add("TREND",20,"EMA 50+200 BUY +20")
    elif price<e50 and price<e200: trend="SELL"; add("TREND",20,"EMA 50+200 SELL +20")
    else: trend="BUY" if price>e50 else "SELL"; add("TREND",5,"EMA Mixed +5"); tags.append("EMA MIXED")
    if htf is not None and htf!=trend: flags.append("1H_CONFLICT"); details.append("FLAG 1H Conflict"); tags.append("1H CONFLICT")
    elif htf is not None and htf==trend: add("TREND",5,f"1H Agrees {htf} +5"); tags.append("1H AGREE")
    if htf4 is not None and htf4==trend: add("TREND",5,f"4H Agrees {htf4} +5"); tags.append("4H AGREE")
    elif htf4 is not None and htf4!=trend: flags.append("4H_CONFLICT"); details.append("FLAG 4H Conflict"); tags.append("4H CONFLICT")
    adx_val,plus_di,minus_di=adx_calc_true_wilder(h15,l15,c15,14)
    if adx_val>=25: add("MOMENTUM",5,f"ADX {adx_val} STRONG +5"); tags.append(f"ADX STRONG {adx_val}")
    elif adx_val>=20: details.append(f"ADX {adx_val} Normal"); tags.append(f"ADX {adx_val}")
    else: flags.append("ADX_WEAK"); details.append(f"FLAG ADX Weak {adx_val}"); tags.append(f"ADX WEAK {adx_val}")
    trend_strength=50; trend_label="NORMAL"
    try:
        dist_pct=abs(price-e50)/price*100 if price!=0 else 0
        ema_series=[]; tmp=c15[0]; k=2/(50+1)
        for v in c15: tmp=v*k+tmp*(1-k); ema_series.append(tmp)
        slope=(ema_series[-1]-ema_series[-5])/ema_series[-5]*100 if len(ema_series)>=6 and ema_series[-5]!=0 else 0
        slope_val=slope if trend=="BUY" else -slope
        trend_strength=min(100,max(0,50+dist_pct*800+slope_val*800))
        if trend_strength>=75: trend_label="STRONG"; add("TREND",5,f"TREND STRONG {trend_strength:.0f}% +5"); tags.append(f"TREND STRONG {trend_strength:.0f}%")
        elif trend_strength>=55: trend_label="NORMAL"; details.append(f"Trend Normal {trend_strength:.0f}%")
        else: trend_label="WEAK"; details.append(f"Trend Weak {trend_strength:.0f}%")
    except Exception as e: details.append(f"Trend Error {e}")
    daily_label="None"; weekly_label="None"; y_high=None; y_low=None
    try:
        if df_1d is not None and len(df_1d)>=2:
            try:
                last_daily_ts=df_1d.index[-1]; last_daily_utc_date=last_daily_ts.tz_convert('UTC').date() if hasattr(last_daily_ts,'tz_convert') else last_daily_ts.date(); today_utc_date=dt.now(pytz.UTC).date(); use_idx=-2 if last_daily_utc_date==today_utc_date else -1
            except: use_idx=-2
            d_high=float(df_1d['High'].iloc[use_idx]); d_low=float(df_1d['Low'].iloc[use_idx]); y_high=d_high; y_low=d_low
            if trend=="BUY" and price>d_high: daily_label="ABOVE DAILY HIGH"; tags.append("ABOVE DAILY HIGH")
            elif trend=="SELL" and price<d_low: daily_label="BELOW DAILY LOW"; tags.append("BELOW DAILY LOW")
            else: daily_label="INSIDE DAILY RANGE"; tags.append("INSIDE DAILY RANGE")
        if df_1w is not None and len(df_1w)>=2:
            try:
                last_wk_ts=df_1w.index[-1]; wk_start=last_wk_ts.date(); monday_this_week=now.date()-timedelta(days=now.weekday()); use_w=-2 if wk_start==monday_this_week else -1
                if len(df_1w)<3 and use_w==-2: use_w=-1
            except: use_w=-2
            w_high=float(df_1w['High'].iloc[use_w]); w_low=float(df_1w['Low'].iloc[use_w])
            if trend=="BUY" and price>w_high: weekly_label="ABOVE WEEKLY HIGH"; tags.append("ABOVE WEEKLY HIGH")
            elif trend=="SELL" and price<w_low: weekly_label="BELOW WEEKLY LOW"; tags.append("BELOW WEEKLY LOW")
            else: weekly_label="INSIDE WEEKLY"
    except Exception as e: details.append(f"Daily/Weekly Error {e}")
    wins=USER_REPORTED_WINS.get(name,0); losses=USER_REPORTED_LOSSES.get(name,0); pwins=PRICE_LEVEL_WINS.get(name,0); plosses=PRICE_LEVEL_LOSSES.get(name,0)
    details.append(f"Pair User: {wins}W {losses}L | Price: {pwins}W {plosses}L")
    sess_label,is_prime=get_session_label(now)
    if is_prime: add("CONTEXT",5,f"{sess_label} +5 - PRIME"); tags.append("PRIME SESSION")
    else: details.append(f"{sess_label} - Tag only"); tags.append(sess_label)
    r=rsi_calc_wilder(c15,14)
    if trend=="BUY" and r>85: flags.append("RSI_EXTREME"); details.append(f"FLAG RSI {r:.1f} extreme")
    elif trend=="SELL" and r<15: flags.append("RSI_EXTREME"); details.append(f"FLAG RSI {r:.1f} extreme")
    details.append(f"RSI {r:.1f}")
    if r>70 and trend=="BUY": tags.append("REVERSAL RISK - RSI High")
    if r<30 and trend=="SELL": tags.append("REVERSAL RISK - RSI Low")
    div_score, div_blocks = rsi_divergence(c15, trend)
    if div_blocks: flags.append("RSI_DIVERGENCE_AGAINST"); details.append("FLAG RSI Divergence Against")
    if div_score>0: add("MOMENTUM",15,"RSI Divergence +15"); tags.append("RSI DIVERGENCE")
    vwap_val = vwap_calc_daily(df_15m)
    if abs(vwap_val - price) < 0.00001: details.append(f"VWAP Neutral {vwap_val:.2f}"); tags.append("VWAP NEUTRAL")
    elif trend=="SELL" and price < vwap_val: add("CONTEXT",10,f"Below VWAP {vwap_val:.2f} +10"); tags.append("BELOW VWAP")
    elif trend=="BUY" and price > vwap_val: add("CONTEXT",10,f"Above VWAP {vwap_val:.2f} +10"); tags.append("ABOVE VWAP")
    else: add("CONTEXT",-10,f"Against VWAP {vwap_val:.2f} -10"); tags.append("AGAINST VWAP"); flags.append("VWAP_AGAINST")
    atr_val=atr_calc_wilder(h5,l5,c5,14)
    if atr_val<=0: atr_val=abs(price*0.001)
    last_8_range=max(h15[-8:])-min(l15[-8:]); is_chop=last_8_range/price<0.004
    if is_chop: flags.append("CHOP"); details.append("FLAG CHOP"); tags.append("CHOP")
    else: details.append(f"Chop OK - Range {last_8_range:.5f}")
    if name=="XAUUSD" and atr_val>8.0: flags.append("GOLD_ATR_HIGH"); details.append(f"FLAG Gold ATR {atr_val:.2f}")
    last3=c5[-1]-c5[-4]; chase=False
    if trend=="BUY" and last3>atr_val*0.6: chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT"); flags.append("CHASE")
    elif trend=="SELL" and last3<-atr_val*0.6: chase=True; warnings.append("CHASE - Use LIMIT"); flags.append("CHASE")
    else: details.append("Anti-Chase OK")
    big=0
    for i in range(-4,-1):
        rng=h5[i]-l5[i]
        if rng>atr_val*0.9:
            if trend=="BUY" and c5[i]>c5[i-1]: big+=1
            if trend=="SELL" and c5[i]<c5[i-1]: big+=1
    if big>=3: warnings.append("EXHAUSTED"); details.append("EXHAUSTION"); tags.append("EXHAUSTED"); flags.append("EXHAUSTED")
    hi_prev=max(h15[-20:-1]); lo_prev=min(l15[-20:-1]); bos_ok=(trend=="BUY" and c15[-1]>hi_prev) or (trend=="SELL" and c15[-1]<lo_prev)
    if bos_ok: add("STRUCTURE",20,"15m BOS +20")
    else: details.append("BOS 0")
    vol_label="No Volume Data"
    try:
        if v15 is not None and len(v15)>=20:
            avg_vol=sum(v15[-20:-1])/19; last_vol=v15[-1]
            if avg_vol>0 and last_vol>avg_vol*1.5: vol_label="HIGH VOLUME"; add("CONTEXT",5,"VOLUME HIGH +5"); tags.append("HIGH VOLUME")
            elif avg_vol>0 and last_vol<avg_vol*0.5: vol_label="LOW VOLUME"; details.append("Volume Low"); tags.append("LOW VOLUME"); flags.append("LOW_VOLUME")
            else: vol_label="NORMAL VOLUME"; details.append("Volume Normal")
    except Exception as e: details.append(f"Volume Error {e}")
    structure_tag="None"
    try:
        last_highs=h15[-20:]; last_lows=l15[-20:]; max_h=max(last_highs); min_l=min(last_lows)
        touches_high=sum(1 for h in last_highs if abs(h-max_h)/max_h<0.001); touches_low=sum(1 for l in last_lows if abs(l-min_l)/min_l<0.001)
        if trend=="SELL" and touches_high>=2: structure_tag="DOUBLE TOP"; tags.append("DOUBLE TOP")
        elif trend=="BUY" and touches_low>=2: structure_tag="DOUBLE BOTTOM"; tags.append("DOUBLE BOTTOM")
        elif bos_ok: structure_tag="CLEAN BREAKOUT"; add("STRUCTURE",5,"CLEAN BREAKOUT +5"); tags.append("CLEAN BREAKOUT")
        else: details.append("Structure: Normal")
    except Exception as e: details.append(f"Structure Error {e}")
    prev_low=min(l5[-20:-2]); prev_high=max(h5[-20:-2]); sweep_ok=False; sweep_bonus=False; sweep_level="Local"; asia_high, asia_low = get_asia_range(df_5m)
    for idx in [-6,-5,-4,-3,-2]:
        sc=df_5m.iloc[idx]
        if trend=="BUY" and sc['Low']<prev_low and sc['Close']>prev_low:
            rng=sc['High']-sc['Low']
            if rng>0 and (sc['Close']-sc['Low'])/rng>0.5:
                sweep_ok=True
                if y_low and abs(sc['Low']-y_low)/price<0.003: sweep_bonus=True; sweep_level="Yesterday Low"
                elif asia_low and abs(sc['Low']-asia_low)/price<0.003: sweep_bonus=True; sweep_level="Asia Low (Real)"
                break
        if trend=="SELL" and sc['High']>prev_high and sc['Close']<prev_high:
            rng=sc['High']-sc['Low']
            if rng>0 and (sc['High']-sc['Close'])/rng>0.5:
                sweep_ok=True
                if y_high and abs(sc['High']-y_high)/price<0.003: sweep_bonus=True; sweep_level="Yesterday High"
                elif asia_high and abs(sc['High']-asia_high)/price<0.003: sweep_bonus=True; sweep_level="Asia High (Real)"
                break
    if sweep_ok:
        add("STRUCTURE",15,f"Sweep +15 ({sweep_level})"); tags.append(f"LIQUIDITY SWEEP {sweep_level}")
        if sweep_bonus: add("STRUCTURE",15,"SMART Sweep +15")
    else: details.append("Sweep 0")
    bos5=False
    for i in range(len(df_5m)-5,len(df_5m)-1):
        if trend=="BUY" and c5[i]>max(h5[i-5:i]): bos5=True
        if trend=="SELL" and c5[i]<min(l5[i-5:i]): bos5=True
    if bos5: add("STRUCTURE",10,"5m BOS +10")
    fvg_found=False; fvg_top=0; fvg_bottom=0; fvg_ok=False; fvg_inside=False; fvg_fresh=False; fvg_distance_ok=True
    for i in range(len(df_5m)-3,max(len(df_5m)-11,2),-1):
        if i<2 or i+2>=len(df_5m): continue
        h1=h5[i]; l1=l5[i]; h3=h5[i+2]; l3=l5[i+2]
        if trend=="BUY" and h1<l3:
            mitigated=False
            for k in range(i+3,len(df_5m)):
                if l5[k]<=l3 and h5[k]>=h1: mitigated=True; break
            if not mitigated: fvg_bottom=h1; fvg_top=l3; fvg_found=True; fvg_fresh=True; break
        if trend=="SELL" and l1>h3:
            mitigated=False
            for k in range(i+3,len(df_5m)):
                if h5[k]>=h3 and l5[k]<=l1: mitigated=True; break
            if not mitigated: fvg_top=l1; fvg_bottom=h3; fvg_found=True; fvg_fresh=True; break
    current=c5[-1]
    if fvg_found:
        fvg_mid=(fvg_top+fvg_bottom)/2; dist_pct=abs(current-fvg_mid)/current if current!=0 else 0
        if dist_pct>0.003: fvg_distance_ok=False; details.append(f"FVG FAR {dist_pct*100:.2f}%"); tags.append("FVG FAR"); flags.append("FVG_FAR"); fvg_ok=False
        elif fvg_bottom<=current<=fvg_top: fvg_inside=True; add("STRUCTURE",20,"FVG INSIDE FRESH +20"); fvg_ok=True
        else: add("STRUCTURE",10,"FVG Near Fresh +10"); fvg_ok=True
    else: details.append("FVG 0")
    obs=find_obs(df_5m,trend); ob_ok=False; ob_type="None"; ob_top=0; ob_bottom=0; ob_mid=0; ob_inside=False; ob_quality="Weak"
    if obs:
        best=None; best_dist=99999
        for o in obs:
            dist=abs(o["mid"]-current)
            if trend=="BUY" and o["mid"]>current+atr_val*0.5: continue
            if trend=="SELL" and o["mid"]<current-atr_val*0.5: continue
            if dist<best_dist: best_dist=dist; best=o
        if best is not None:
            ob_ok=True; ob_type=best["type"]; ob_top=best["top"]; ob_bottom=best["bottom"]; ob_mid=best["mid"]; ob_inside=ob_bottom<=current<=ob_top
            if "BREAKER" in ob_type: add("STRUCTURE",20,f"BREAKER {ob_type} +20"); ob_quality="STRONG Breaker"
            elif "MITIGATION" in ob_type: add("STRUCTURE",18,f"MITIGATION {ob_type} +18"); ob_quality="STRONG Mitigation"
            elif best.get("fresh",False): add("STRUCTURE",15,f"OB {ob_type} FRESH +15"); ob_quality="FRESH OB"
            else: add("STRUCTURE",10,f"OB {ob_type} OLD +10"); ob_quality="OLD OB"
            if ob_inside: add("STRUCTURE",5,"OB INSIDE +5")
    else: details.append("OB 0")
    last_c=df_5m.iloc[-1]; last_rng=last_c['High']-last_c['Low']; rej=False
    if last_rng>0:
        if trend=="BUY" and (last_c['Close']-last_c['Low'])/last_rng>0.4: rej=True
        if trend=="SELL" and (last_c['High']-last_c['Close'])/last_rng>0.4: rej=True
    if rej: add("STRUCTURE",10,"Rejection 5m +10")
    m1_ok=False
    if df_1m is not None and len(df_1m)>=3:
        try:
            m1_last=df_1m.iloc[-1]; m1_rng=m1_last['High']-m1_last['Low']
            if m1_rng>0:
                if trend=="BUY" and (m1_last['Close']-m1_last['Low'])/m1_rng>0.4: m1_ok=True
                if trend=="SELL" and (m1_last['High']-m1_last['Close'])/m1_rng>0.4: m1_ok=True
            if m1_ok: add("MOMENTUM",10,"1m Rejection CONFIRM +10")
            else: details.append("1m No confirm")
        except: details.append("1m Error")
    raw_score = sum(cat.values())
    capped = sum(min(cat[k], CAT_CAPS[k]) for k in cat)
    conf=sum([1 for x in [bos_ok,sweep_ok,fvg_ok,ob_ok] if x])
    if conf<2: flags.append("LOW_CONFLUENCE"); details.append(f"LOW CONFLUENCE {conf}/4")
    if capped < 48 and len(flags) < 2:
        return {"fail":True,"score":capped,"raw_score":raw_score,"capped":capped,"reason":f"Score {capped} < 48","trend":trend,"details":details,"flags":flags,"cats":cat}
    if ob_ok and fvg_found and fvg_distance_ok:
        if ob_inside or abs(ob_mid-current)<abs(((fvg_top+fvg_bottom)/2)-current): entry=ob_mid; entry_type=f"OB {ob_type}"
        else: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG FRESH"
    elif ob_ok: entry=ob_mid; entry_type=f"OB {ob_type}"
    elif fvg_found and fvg_distance_ok: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG FRESH"
    else: entry=current; entry_type="Live Price"
    tech_entry=entry
    if abs(entry-current)/current<0.0005:
        order_type="BUY MARKET" if trend=="BUY" else "SELL MARKET"; monitor_entry=current; entry=monitor_entry; entry_type=entry_type+" - MARKET"
    else:
        if trend=="BUY" and entry<current: order_type="BUY LIMIT"; monitor_entry=entry
        elif trend=="SELL" and entry>current: order_type="SELL LIMIT"; monitor_entry=entry
        elif trend=="BUY" and entry>current: order_type="BUY MARKET"; monitor_entry=current; entry=current; entry_type="Live Price - Corrected"
        elif trend=="SELL" and entry<current: order_type="SELL MARKET"; monitor_entry=current; entry=current; entry_type="Live Price - Corrected"
        else: order_type="BUY MARKET" if trend=="BUY" else "SELL MARKET"; monitor_entry=current; entry=current
    buf = SL_BUFFER.get(name, 0); fvg_for_sl=fvg_found and fvg_distance_ok
    if trend=="BUY":
        struct_sl = (ob_bottom - atr_val*0.2) if ob_ok else ((fvg_bottom - atr_val*0.2) if fvg_for_sl else None)
        atr_sl = entry - atr_val*SL_ATR_MULT - buf; sl = min(struct_sl, atr_sl) if struct_sl is not None else atr_sl; risk = entry - sl
    else:
        struct_sl = (ob_top + atr_val*0.2) if ob_ok else ((fvg_top + atr_val*0.2) if fvg_for_sl else None)
        atr_sl = entry + atr_val*SL_ATR_MULT + buf; sl = max(struct_sl, atr_sl) if struct_sl is not None else atr_sl; risk = sl - entry
    if risk<=0: return {"fail":True,"hard":True,"score":capped,"reason":"Risk <=0","trend":trend,"flags":flags}
    if name in YAHOO_PROXY_PAIRS: risk = risk * 1.2
    off = OFFSETS.get(name,0.0); entry_b = entry + off; sl_b = sl + off
    tp_b = entry_b + risk*0.7 if trend=="BUY" else entry_b - risk*0.7
    spread = SPREAD.get(name,0)
    eff_risk = risk + spread
    eff_tp_dist = risk*0.7 - spread
    net_rr = (eff_tp_dist / eff_risk) if eff_risk>0 else 0
    if net_rr < 0.50: flags.append("LOW_NET_RR"); details.append(f"FLAG Net R:R {net_rr:.2f} < 0.50")
    if risk>0 and spread/risk > 0.30: flags.append("SPREAD_HIGH"); details.append(f"FLAG Spread {spread:.5f} >30% of risk {risk:.5f}")
    max_r=MAX_RISK.get(name,9999)
    if risk > max_r*1.5:
        return {"fail":True,"hard":True,"score":capped,"reason":f"HARD SL Cap {risk:.5f} > {max_r*1.5:.5f} - Insane","trend":trend,"flags":flags,"details":details}
    if risk > max_r:
        flags.append("SL_WIDE"); details.append(f"SL Wide {risk:.5f} > {max_r} - 0.01 only")
    if risk/entry>0.01: return {"fail":True,"hard":True,"score":capped,"reason":"HARD SL too wide","trend":trend,"flags":flags,"details":details}
    min_sl = MIN_SL.get(name, 0.00040)
    if risk < min_sl:
        flags.append("SL_TIGHT"); details.append(f"SL Tight {risk:.5f} < {min_sl} - 0.01 only")
    flags = list(dict.fromkeys(flags)); final_score = capped - len(flags)*5
    print(f"{name} {trend} Raw {raw_score} Capped {capped} Final {final_score} Flags {flags} RSI {r:.1f} ADX {adx_val} OB {ob_type} FVG {fvg_fresh} H1 {htf} VWAP {vwap_val:.2f} Cats {cat}",flush=True)
    if len(flags)==0 and final_score>=90 and conf>=3 and not chase and (fvg_inside or ob_inside) and rej and m1_ok: tier="S-TIER"; tier_label="S"; take_it="YES - BEST"
    elif len(flags)==0 and final_score>=80 and conf>=3 and not chase: tier="A-TIER"; tier_label="A"; take_it="YES - Full"
    elif len(flags)==1: tier="REDUCED RISK"; tier_label="R"; take_it=f"YES - 0.01 only - Flag: {flags[0]}"
    else: tier="SHADOW"; tier_label="SH"; take_it="Shadow only"
    log_features({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trend":trend,"score":final_score,"raw_score":raw_score,"capped":capped,"tier":tier_label,"flags":"|".join(flags),"rsi":round(float(r),1),"adx":adx_val,"vwap":round(float(vwap_val),2),"htf":htf,"htf4":htf4,"sess":sess_label,"structure":structure_tag,"sweep":sweep_level if sweep_ok else "None","ob":ob_type,"fvg":fvg_fresh,"entry":round(float(entry_b),5),"sl":round(float(sl_b),5),"tp":round(float(tp_b),5),"risk":round(float(risk),5),"net_rr":round(float(net_rr),2)})
    return {"symbol":name,"trend":trend,"order_type":order_type,"entry":round(float(entry_b),5),"tech_entry":round(float(tech_entry+off),5),"monitor_entry":round(float(monitor_entry+off),5),"sl":round(float(sl_b),5),"tp":round(float(tp_b),5),"score":final_score,"raw_score":raw_score,"capped":capped,"cats":cat,"details":details,"tier":tier,"tier_label":tier_label,"take_it":take_it,"live_price":round(float(current+off),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"entry_type":entry_type,"ob_type":ob_type,"ob_quality":ob_quality,"warnings":warnings,"chase":chase,"fvg_inside":fvg_inside,"fvg_fresh":fvg_fresh,"sl_mult":SL_ATR_MULT,"sweep_level":sweep_level if sweep_ok else "None","m1_ok":m1_ok,"tags":tags,"sess_label":sess_label,"is_prime":is_prime,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"adx":adx_val,"vol_label":vol_label,"daily_label":daily_label,"weekly_label":weekly_label,"risk_dist":round(float(risk),5),"vwap":round(float(vwap_val),5),"offset":off,"flags":flags,"atr":atr_val,"net_rr":round(float(net_rr),2)}
def bot_loop():
    global TOTAL_SIGNALS_TODAY, TOTAL_SHADOW_TODAY
    load_outcomes()
    send_telegram("V39 LIVE - 24/5\n12 Pairs | Mon-Fri 24h | Weekend OFF | SINGLE TP 0.7R | SHADOW Fix | LIMIT STALE Fix")
    sent=set(); last_sent_time={}
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz); hour=now.hour+now.minute/60.0
            maybe_reset_daily(now)
            # V39: 24/5 - Mon-Fri 24h, Weekend OFF - NO hour block, NO XAUUSD hour block
            if now.weekday()>=5: time.sleep(60); continue
            if is_news_time(now): print(f"NEWS BLOCK {now}",flush=True); time.sleep(60); continue
            sess_label,is_prime=get_session_label(now)
            print(f"V39 checking {now} Session {sess_label} Active {len(ACTIVE_TRADES)} Shadow {len(SHADOW_TRADES)} Signals {TOTAL_SIGNALS_TODAY}/999 Shadow {TOTAL_SHADOW_TODAY}/999",flush=True)
            candidates=[]
            for name,ysym in SYMBOLS.items():
                try:
                    if name in PAIR_COOLDOWN_UNTIL and time.time()<PAIR_COOLDOWN_UNTIL[name]: continue
                    if any(t["pair"]==name for t in ACTIVE_TRADES): continue
                    if name in PAIR_LAST_SIGNAL_TIME and (time.time()-PAIR_LAST_SIGNAL_TIME[name]) < COOLDOWN_MIN*60: continue
                    res=analyze(name,ysym,now)
                    if res and res.get("fail"):
                        if res.get("hard"):
                            print(f"HARD BLOCK {name} {res.get('reason')} Flags {res.get('flags')}",flush=True); VETO_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trend":res.get("trend"),"score":res.get("score",0),"reason":res.get("reason")}); save_outcomes()
                        else: print(f"LOW SCORE {name} {res.get('reason')} Flags {res.get('flags')}",flush=True)
                        continue
                    if res and not res.get("fail"): candidates.append(res)
                except Exception as e: print(f"Error {name}: {e}",flush=True); traceback.print_exc()
            candidates.sort(key=lambda x: x.get("raw_score", x.get("score",0)), reverse=True)
            print(f"V39 candidates ranked: {[(c['symbol'], c['raw_score'], c['flags']) for c in candidates]}",flush=True)
            used_exposure=set(); sent_this_loop=0
            for res in candidates:
                name=res['symbol']; trend=res['trend']; flags=res.get("flags",[])
                if PAIR_LOSS_TODAY.get(name,0)>0 and len(flags)>0:
                    sid=new_shadow_id(); SHADOW_TRADES.append({"pair":name,"shadow_id":sid,"trend":trend,"entry":res['entry'],"sl":res['sl'],"tp":res['tp'],"time":time.time(),"status":"SHADOW_PENDING","flags":flags+["PAIR_LOST_TODAY"],"last_processed":time.time(),"best_favorable":res['entry'],"almost_tp_alerted":False}); TOTAL_SHADOW_TODAY+=1; save_outcomes(); continue
                blocked=False
                for grp, pairs in EXPOSURE.items():
                    if name in pairs:
                        key=f"{grp}_{trend}"
                        if key in used_exposure: blocked=True; break
                if blocked:
                    if len(flags)<2:
                        sid=new_shadow_id(); SHADOW_TRADES.append({"pair":name,"shadow_id":sid,"trend":trend,"entry":res['entry'],"sl":res['sl'],"tp":res['tp'],"time":time.time(),"status":"SHADOW_PENDING","flags":flags+["EXPOSURE"],"last_processed":time.time(),"best_favorable":res['entry'],"almost_tp_alerted":False}); TOTAL_SHADOW_TODAY+=1; save_outcomes()
                    continue
                # V39 FIX: SHADOW tier must NEVER send, even with 0 flags
                if res.get("tier_label")=="SH" or len(flags)>=2:
                    sid=new_shadow_id(); SHADOW_TRADES.append({"pair":name,"shadow_id":sid,"trend":trend,"entry":res['entry'],"sl":res['sl'],"tp":res['tp'],"time":time.time(),"status":"SHADOW_PENDING","flags":flags,"last_processed":time.time(),"best_favorable":res['entry'],"almost_tp_alerted":False}); TOTAL_SHADOW_TODAY+=1
                    for grp, pairs in EXPOSURE.items():
                        if name in pairs: used_exposure.add(f"{grp}_{trend}")
                    save_outcomes(); continue
                if sent_this_loop>=2:
                    sid=new_shadow_id(); SHADOW_TRADES.append({"pair":name,"shadow_id":sid,"trend":trend,"entry":res['entry'],"sl":res['sl'],"tp":res['tp'],"time":time.time(),"status":"SHADOW_PENDING","flags":flags+["OVERFLOW"],"last_processed":time.time(),"best_favorable":res['entry'],"almost_tp_alerted":False}); TOTAL_SHADOW_TODAY+=1; save_outcomes(); continue
                cool=f"{name}-{trend}-{res['tier_label']}"
                if cool in last_sent_time and (time.time()-last_sent_time[cool])<1800: continue
                key=f"{name}-{trend}-{res['entry']}-{res['tier_label']}"
                if key in sent: continue
                trade_id=new_trade_id(); flag_txt=""
                if len(flags)==1: flag_txt=f"\n⚠️ REDUCED RISK - 1 Flag: {flags[0]}\nLot: 0.01 only"
                elif len(flags)==0: flag_txt="\n✅ CLEAN - No flags"
                yahoo_warn = f"\n⚠️ OFFSET {res.get('offset',0)} applied" if res.get('offset',0)!=0 else ""
                if res['symbol'] in YAHOO_PROXY_PAIRS: yahoo_warn += "\n⚠️ YAHOO PROXY - CHECK BROKER PRICE!"
                cats=res.get('cats',{})
                # V39 FIX: removed duplicate trend - was "SELL SELL LIMIT"
                msg=f"SIGNAL - ID {trade_id}\nPair: {res['symbol']} {res['order_type']}\nEntry: {res['entry']} | SL: {res['sl']} | TP: {res['tp']} (0.7R)\nScore: Raw {res.get('raw_score')} | Final {res['score']}\nWhy: Structure {cats.get('STRUCTURE',0)}/50 Trend {cats.get('TREND',0)}/25 Momentum {cats.get('MOMENTUM',0)}/25 Context {cats.get('CONTEXT',0)}/20\nTier: {res['tier']} | {res.get('take_it')}{flag_txt}\nReason: {res.get('structure_tag','')} | {res.get('sess_label','')} | Net R:R {res.get('net_rr')} after spread{yahoo_warn}\n⏰ If TP is not reached within 20 minutes after entry touch, close/BE. Don't hold 2h.\nTap ✅ CONFIRM or type: CONFIRM {trade_id}"
                delivered=send_telegram(msg, trade_id=trade_id)
                if not delivered: continue
                for grp, pairs in EXPOSURE.items():
                    if name in pairs: used_exposure.add(f"{grp}_{trend}")
                PAIR_SIGNAL_COUNT[name]=PAIR_SIGNAL_COUNT.get(name,0)+1; PAIR_LAST_SIGNAL_TIME[name]=time.time(); TOTAL_SIGNALS_TODAY+=1; sent_this_loop+=1; sent.add(key); last_sent_time[cool]=time.time()
                OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade_id,"tier":res['tier_label'],"trend":res['trend'],"score":res['score'],"raw_score":res.get('raw_score'),"flags":flags,"entry":res['entry'],"sl":res['sl'],"tp":res['tp'],"status":"PENDING_CONFIRM"})
                ACTIVE_TRADES.append({"pair":name,"trade_id":trade_id,"trend":res['trend'],"entry":res['entry'],"tech_entry":res.get('tech_entry'),"sl":res['sl'],"tp":res['tp'],"time":time.time(),"tier":res['tier_label'],"best_favorable":res['entry'],"almost_tp_alerted":False,"be_done":False,"status":"PENDING_CONFIRM","effective_sl":res['sl'],"last_processed":time.time(),"live_price":res.get('live_price'),"order_type":res.get('order_type'),"atr":res.get('atr',0),"flags":flags})
                save_outcomes()
            if len(sent)>200: sent.clear()
            gc.collect(); time.sleep(60)
        except Exception as e: print(f"Loop crash {e}",flush=True); traceback.print_exc(); time.sleep(10)
if __name__=="__main__":
    load_outcomes()
    threading.Thread(target=heartbeat,daemon=True).start()
    threading.Thread(target=poll_telegram_loop,daemon=True).start()
    threading.Thread(target=check_active_trades_loop,daemon=True).start()
    threading.Thread(target=bot_loop,daemon=True).start()
    print("V39 - Flask MAIN",flush=True); run_web()
