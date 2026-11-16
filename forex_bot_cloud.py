import os, time, requests, threading, pytz, gc, traceback, json
from datetime import datetime as dt, timedelta
import yfinance as yf
from flask import Flask

app = Flask(__name__)
@app.route('/')
def home(): return "V37.1 ALIVE", 200
@app.route('/health')
def health(): return "OK", 200
@app.route('/<path:path>')
def catch_all(path): return "V37.1 ALIVE", 200
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")

SYMBOLS = {"XAUUSD":"GC=F","GBPJPY":"GBPJPY=X","US30":"YM=F","GBPUSD":"GBPUSD=X","EURUSD":"EURUSD=X","EURJPY":"EURJPY=X","USDJPY":"USDJPY=X","AUDUSD":"AUDUSD=X","USDCAD":"USDCAD=X","NZDUSD":"NZDUSD=X","USDCHF":"USDCHF=X","EURGBP":"EURGBP=X"}
MAX_RISK = {"XAUUSD":5.0,"GBPJPY":0.8,"US30":80.0,"GBPUSD":0.005,"EURUSD":0.005,"EURJPY":0.8,"USDJPY":0.8,"AUDUSD":0.005,"USDCAD":0.005,"NZDUSD":0.005,"USDCHF":0.005,"EURGBP":0.005}
PAIR_BONUS = {k:0 for k in SYMBOLS}
MIN_SL = {"XAUUSD":2.0,"GBPJPY":0.08,"US30":30.0,"GBPUSD":0.00040,"EURUSD":0.00040,"EURJPY":0.08,"USDJPY":0.08,"AUDUSD":0.00040,"USDCAD":0.00040,"NZDUSD":0.00040,"USDCHF":0.00040,"EURGBP":0.00040}
# V37.1 FIX 5: Single source of truth for SL
SL_ATR_MULT = 1.5
# V37.1 FIX 6: Yahoo futures proxies
YAHOO_PROXY_PAIRS = {"XAUUSD","US30"}

PAIR_COOLDOWN_UNTIL = {}
LAST_SIGNAL_PRICE = {}
PAIR_SIGNAL_COUNT = {}
PRICE_LEVEL_WINS = {}
PRICE_LEVEL_LOSSES = {}
USER_REPORTED_WINS = {}
USER_REPORTED_LOSSES = {}
PAIR_LAST_SIGNAL_TIME = {}
OUTCOME_LOG = []
# V37.1 FIX 3: Separate logs
PRICE_OUTCOMES = []
USER_OUTCOMES = []
ACTIVE_TRADES = []
PENDING_USER_REPORTS = []
VETO_LOG = []
TRADE_HISTORY_FILE = "trade_history.json"
TELEGRAM_OFFSET = 0
LAST_PROCESSED_1M = {}
TRADE_COUNTER = 0
LAST_RESET_DATE = ""
COMPLETED_TRADE_IDS = set()

def save_outcomes():
    # V37.1 FIX 4: Atomic save via tmp + os.replace
    try:
        data = {"price_wins":PRICE_LEVEL_WINS,"price_losses":PRICE_LEVEL_LOSSES,"user_wins":USER_REPORTED_WINS,"user_losses":USER_REPORTED_LOSSES,"outcomes":OUTCOME_LOG[-200:],"veto_log":VETO_LOG[-200:],"price_outcomes":PRICE_OUTCOMES[-200:],"user_outcomes":USER_OUTCOMES[-200:],"active":ACTIVE_TRADES,"pending_reports":PENDING_USER_REPORTS,"offset":TELEGRAM_OFFSET,"last_processed":LAST_PROCESSED_1M,"trade_counter":TRADE_COUNTER,"completed_ids":list(COMPLETED_TRADE_IDS),"last_reset":LAST_RESET_DATE,"signal_count":PAIR_SIGNAL_COUNT,"cooldown":PAIR_COOLDOWN_UNTIL}
        tmp = TRADE_HISTORY_FILE + ".tmp"
        with open(tmp,"w") as f:
            json.dump(data,f)
        os.replace(tmp, TRADE_HISTORY_FILE)
    except Exception as e: print(f"Save error {e}",flush=True)
def load_outcomes():
    global PRICE_LEVEL_WINS,PRICE_LEVEL_LOSSES,USER_REPORTED_WINS,USER_REPORTED_LOSSES,OUTCOME_LOG,PRICE_OUTCOMES,USER_OUTCOMES,VETO_LOG,ACTIVE_TRADES,PENDING_USER_REPORTS,TELEGRAM_OFFSET,LAST_PROCESSED_1M,TRADE_COUNTER,COMPLETED_TRADE_IDS,LAST_RESET_DATE,PAIR_SIGNAL_COUNT,PAIR_COOLDOWN_UNTIL
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE,"r") as f:
                d=json.load(f)
                PRICE_LEVEL_WINS=d.get("price_wins",{}); PRICE_LEVEL_LOSSES=d.get("price_losses",{})
                USER_REPORTED_WINS=d.get("user_wins",{}); USER_REPORTED_LOSSES=d.get("user_losses",{})
                OUTCOME_LOG=d.get("outcomes",[]); VETO_LOG=d.get("veto_log",[]); PRICE_OUTCOMES=d.get("price_outcomes",[]); USER_OUTCOMES=d.get("user_outcomes",[])
                ACTIVE_TRADES=d.get("active",[]); PENDING_USER_REPORTS=d.get("pending_reports",[])
                TELEGRAM_OFFSET=d.get("offset",0); LAST_PROCESSED_1M=d.get("last_processed",{}); TRADE_COUNTER=d.get("trade_counter",0)
                COMPLETED_TRADE_IDS=set(d.get("completed_ids",[])); LAST_RESET_DATE=d.get("last_reset",""); PAIR_SIGNAL_COUNT=d.get("signal_count",{}); PAIR_COOLDOWN_UNTIL=d.get("cooldown",{})
    except Exception as e:
        print(f"Load error {e} - starting fresh, not crashing",flush=True)
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",json={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=10)
        try:
            j=r.json(); return r.status_code==200 and j.get("ok",False)
        except: return False
    except: return False
def heartbeat():
    while True:
        print(f"HEARTBEAT {dt.now(pytz.timezone('Africa/Nairobi'))}",flush=True); time.sleep(30)
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
def get_fresh_price(ysym):
    # V37.1 FIX 1: Fresh price at CONFIRM
    try:
        df=get_data(ysym,"1m","1d")
        if df is None or len(df)==0: return None
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
def is_news_time(now):
    if now.weekday()>=5: return True
    if 15.0<=(now.hour+now.minute/60.0)<16.5: return True
    if now.weekday()==4 and now.day<=7 and 15.0<=(now.hour+now.minute/60.0)<17.0: return True
    return False
def get_session_label(now):
    hour=now.hour+now.minute/60.0
    if 9.0<=hour<12.0: return "LONDON PRIME",True
    if 15.0<=hour<19.0: return "NY PRIME",True
    if 12.0<=hour<15.0: return "CHOP TIME",False
    if 19.0<=hour<21.0: return "LATE - Careful",False
    return "OFF",False
def maybe_reset_daily(now):
    global PAIR_SIGNAL_COUNT,LAST_RESET_DATE
    today=now.strftime("%Y-%m-%d")
    if LAST_RESET_DATE!=today:
        PAIR_SIGNAL_COUNT={}; PAIR_LAST_SIGNAL_TIME.clear(); LAST_RESET_DATE=today; save_outcomes()
def new_trade_id():
    global TRADE_COUNTER; TRADE_COUNTER+=1; save_outcomes(); return f"T{TRADE_COUNTER:03d}"
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

def poll_telegram_commands():
    global TELEGRAM_OFFSET,ACTIVE_TRADES,PENDING_USER_REPORTS
    if not TELEGRAM_TOKEN: return
    try:
        r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params={"offset":TELEGRAM_OFFSET,"timeout":5},timeout=10)
        if r.status_code!=200: return
        data=r.json()
        for upd in data.get("result",[]):
            TELEGRAM_OFFSET=upd["update_id"]+1
            msg=upd.get("message",{}); chat_id=str(msg.get("chat",{}).get("id",""))
            if not TELEGRAM_CHAT_ID or not TELEGRAM_USER_ID: continue
            if chat_id!=str(TELEGRAM_CHAT_ID): continue
            from_id=str(msg.get("from",{}).get("id",""))
            if from_id!=str(TELEGRAM_USER_ID): continue
            text=(msg.get("text") or "").strip().upper()
            if not text: continue
            parts=text.split(); cmd=parts[0]; trade_id=None
            for p in parts[1:]:
                if p.startswith("T") and len(p)>=4 and p[1:].isdigit(): trade_id=p; break
            def find_trade():
                if not trade_id: return None,None
                for t in ACTIVE_TRADES:
                    if t.get("trade_id")==trade_id: return t,"ACTIVE"
                for t in PENDING_USER_REPORTS:
                    if t.get("trade_id")==trade_id: return t,"PENDING_REPORT"
                return None,None
            def require_id():
                if not trade_id:
                    send_telegram(f"Need ID. Use: {cmd} T001"); return False
                return True
            if cmd=="CONFIRM":
                if not require_id(): continue
                t,where=find_trade()
                if not t:
                    send_telegram(f"CONFIRM FAILED - No active {trade_id}"); continue
                if where=="PENDING_REPORT":
                    send_telegram(f"CONFIRM FAILED - {trade_id} closed. Use WIN/LOSS."); continue
                if t.get("status")!="PENDING_CONFIRM":
                    send_telegram(f"Already confirmed - {trade_id}"); continue
                t["status"]="PENDING_FILL"; t["confirm_time"]=time.time(); t["confirm_ts"]=time.time(); t["last_processed"]=time.time()
                # V37.1 FIX 1: Fresh price at CONFIRM
                fresh=get_fresh_price(SYMBOLS.get(t["pair"]))
                t["confirm_price"]=fresh if fresh is not None else t.get("live_price", t["entry"])
                t["confirm_price_time"]=time.time()
                LAST_PROCESSED_1M[t["pair"]]=time.time()
                send_telegram(f"✅ CONFIRMED {trade_id} {t['pair']} {t['trend']}\nEntry {t['entry']} | Now {t['confirm_price']:.5f}")
                save_outcomes()
            elif cmd=="BE":
                # V37.1 FIX 2: BE must be confirmed by user, not assumed
                if not require_id(): continue
                t,where=find_trade()
                if not t:
                    send_telegram(f"BE FAILED - No {trade_id}"); continue
                if t.get("be_done"):
                    send_telegram(f"BE already done - {trade_id}"); continue
                t["be_done"]=True
                send_telegram(f"✅ BE DONE {trade_id} - Bot now uses Entry as SL")
                save_outcomes()
            elif cmd=="WIN":
                if not require_id(): continue
                if trade_id in COMPLETED_TRADE_IDS:
                    send_telegram(f"Duplicate WIN - {trade_id}"); continue
                t,where=find_trade()
                if not t:
                    send_telegram(f"WIN FAILED - No {trade_id}"); continue
                pair=t["pair"]; tid=t.get("trade_id")
                USER_REPORTED_WINS[pair]=USER_REPORTED_WINS.get(pair,0)+1
                if tid: COMPLETED_TRADE_IDS.add(tid)
                ACTIVE_TRADES=[x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]; PENDING_USER_REPORTS=[x for x in PENDING_USER_REPORTS if x.get("trade_id")!=tid]
                # V37.1 FIX 3: Separate USER_OUTCOMES, r=None
                USER_OUTCOMES.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER WIN","r":None,"source":"BROKER - user reported"})
                OUTCOME_LOG.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER-REPORTED WIN","r":None})
                send_telegram(f"✅ WIN {tid} {pair}")
                save_outcomes()
            elif cmd=="LOSS":
                if not require_id(): continue
                if trade_id in COMPLETED_TRADE_IDS:
                    send_telegram(f"Duplicate LOSS - {trade_id}"); continue
                t,where=find_trade()
                if not t:
                    send_telegram(f"LOSS FAILED - No {trade_id}"); continue
                pair=t["pair"]; tid=t.get("trade_id")
                USER_REPORTED_LOSSES[pair]=USER_REPORTED_LOSSES.get(pair,0)+1
                if tid: COMPLETED_TRADE_IDS.add(tid)
                ACTIVE_TRADES=[x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]; PENDING_USER_REPORTS=[x for x in PENDING_USER_REPORTS if x.get("trade_id")!=tid]
                USER_OUTCOMES.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER LOSS","r":None,"source":"BROKER - user reported"})
                OUTCOME_LOG.append({"time":dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"),"pair":pair,"trade_id":tid,"result":"USER-REPORTED LOSS","r":None})
                send_telegram(f"❌ LOSS {tid} {pair} - Paused 2h")
                PAIR_COOLDOWN_UNTIL[pair]=time.time()+7200; save_outcomes()
            elif cmd in ["FILL","SKIP"]:
                send_telegram(f"V37.1: Use CONFIRM / BE / WIN / LOSS")
                continue
        save_outcomes()
    except Exception as e: print(f"Poll error {e}",flush=True)

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
            status=trade.get("status","PENDING_CONFIRM")
            if status=="PENDING_CONFIRM":
                age=(time.time()-trade["time"])/60
                if age>10:
                    atomic_move(trade,"EXPIRED - No Confirm")
                    OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"EXPIRED - No Confirm","entry":trade["entry"]})
                else: remaining.append(trade)
                continue
            if status=="PENDING_FILL":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                confirm_ts=trade.get("confirm_ts",trade.get("confirm_time",trade["time"]))
                window_end=confirm_ts+600; touched=False; touch_row=None; touch_idx=None; found_late=False
                confirm_price=trade.get("confirm_price", trade.get("live_price", trade["entry"]))
                for idx,row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts<confirm_ts-5: continue
                    if ts<confirm_ts: continue
                    if ts>window_end:
                        low_l=float(row['Low']); high_l=float(row['High'])
                        if (trade["trend"]=="BUY" and low_l<=trade["entry"]) or (trade["trend"]=="SELL" and high_l>=trade["entry"]): found_late=True
                        continue
                    low=float(row['Low']); high=float(row['High']); close=float(row['Close'])
                    if trade["trend"]=="BUY":
                        if confirm_price <= trade["entry"]: continue
                        if low<=trade["entry"] and close>trade["sl"]:
                            touched=True; touch_row=row; touch_idx=idx; break
                    else:
                        if confirm_price >= trade["entry"]: continue
                        if high>=trade["entry"] and close<trade["sl"]:
                            touched=True; touch_row=row; touch_idx=idx; break
                if touched:
                    if touch_row is not None:
                        high_e=float(touch_row['High']); low_e=float(touch_row['Low']); entry_v=trade["entry"]; sl_v=trade["sl"]; tp1_v=trade["tp1"]
                        if trade["trend"]=="BUY": same=(low_e<=sl_v) or (high_e>=tp1_v)
                        else: same=(high_e>=sl_v) or (low_e<=tp1_v)
                        if same:
                            send_telegram(f"⚠️ SAME CANDLE - {name} {trade.get('trade_id')} Check broker")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"SAME CANDLE UNVERIFIED","entry":entry_v})
                            atomic_move(trade,"SAME CANDLE UNVERIFIED"); continue
                    trade["status"]="PRICE_TOUCHED"; trade["touch_time"]=time.time(); trade["entry_candle_ts"]=touch_idx.timestamp() if hasattr(touch_idx,'timestamp') else time.time(); trade["last_processed"]=trade["entry_candle_ts"]; LAST_PROCESSED_1M[name]=trade["last_processed"]
                    send_telegram(f"🔔 TOUCHED {name} {trade.get('trade_id')} Entry {trade['entry']}")
                    remaining.append(trade)
                else:
                    confirm_t=trade.get("confirm_time",trade["time"]); age=(time.time()-confirm_t)/60
                    if len(df_completed)>0: trade["last_processed"]=df_completed.index[-1].timestamp() if hasattr(df_completed.index[-1],'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]
                    if age>10:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"EXPIRED - No Touch 10m","entry":trade["entry"]})
                        atomic_move(trade,"EXPIRED - No Touch 10m")
                    else: remaining.append(trade)
                continue
            if status=="PRICE_TOUCHED":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                last_ts=trade.get("last_processed",trade.get("confirm_ts",time.time()-3600))
                candles=[]
                for idx,row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts>last_ts: candles.append((idx,row))
                if not candles:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20 and not trade.get("tp1_hit"):
                        send_telegram(f"⏰ TIMEOUT {name} {trade.get('trade_id')} Reply WIN/LOSS")
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TIMEOUT 20m","entry":trade["entry"]})
                        atomic_move(trade,"TIMEOUT 20m")
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"WATCH EXPIRED","entry":trade["entry"]})
                        atomic_move(trade,"WATCH EXPIRED")
                    else: remaining.append(trade)
                    continue
                trade_done=False
                for idx,row in candles:
                    if trade_done: break
                    high=float(row['High']); low=float(row['Low']); close=float(row['Close'])
                    entry=trade["entry"]; sl=trade["sl"]; tp1=trade["tp1"]; tp2=trade["tp2"]; trend=trade["trend"]; tp1_hit=trade.get("tp1_hit",False)
                    # V37.1 FIX 2: effective SL = entry ONLY if be_done, else original SL
                    be_done=trade.get("be_done", False)
                    effective_sl=entry if be_done else sl
                    if trend=="BUY": tp1_t=high>=tp1; tp2_t=high>=tp2; sl_t=low<=effective_sl; orig_sl_t=low<=sl; be_t=low<=entry if tp1_hit else False
                    else: tp1_t=low<=tp1; tp2_t=low<=tp2; sl_t=high>=effective_sl; orig_sl_t=high>=sl; be_t=high>=entry if tp1_hit else False
                    if not tp1_hit:
                        if tp1_t and sl_t:
                            send_telegram(f"⚠️ TP1+SL SAME CANDLE {name} {trade.get('trade_id')} Check broker")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP1+SL UNVERIFIED","entry":entry}); atomic_move(trade,"TP1+SL UNVERIFIED"); trade_done=True; break
                        if tp1_t and tp2_t:
                            send_telegram(f"🎉 TP1+TP2 {name} {trade.get('trade_id')} Reply WIN")
                            PRICE_LEVEL_WINS[name]=PRICE_LEVEL_WINS.get(name,0)+1
                            PRICE_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"PRICE TP1+TP2","r":1.15,"source":"YAHOO PRICE-LEVEL ONLY"})
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP1+TP2 SAME CANDLE","entry":entry,"r":1.15}); atomic_move(trade,"TP1+TP2 SAME CANDLE"); trade_done=True; break
                        if tp1_t:
                            trade["tp1_hit"]=True
                            send_telegram(f"🔔 TP1 HIT {name} {trade.get('trade_id')}\nClose 50% + Move SL to Entry on broker\nThen reply: BE {trade.get('trade_id')}")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP1 HIT","entry":entry})
                            trade["last_processed"]=idx.timestamp() if hasattr(idx,'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]; continue
                        if sl_t:
                            send_telegram(f"❌ SL HIT {name} {trade.get('trade_id')} Reply LOSS")
                            PRICE_LEVEL_LOSSES[name]=PRICE_LEVEL_LOSSES.get(name,0)+1; PAIR_COOLDOWN_UNTIL[name]=time.time()+7200
                            PRICE_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"PRICE SL","r":-1.0,"source":"YAHOO PRICE-LEVEL ONLY"})
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"SL -1R","entry":entry,"r":-1.0}); atomic_move(trade,"SL -1R"); trade_done=True; break
                        trade["last_processed"]=idx.timestamp() if hasattr(idx,'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]
                    else:
                        if be_done and be_t and orig_sl_t:
                            send_telegram(f"⚠️ BE+SL CONFLICT {name} {trade.get('trade_id')} Check broker")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"BE+SL CONFLICT UNVERIFIED","entry":entry}); atomic_move(trade,"BE+SL CONFLICT UNVERIFIED"); trade_done=True; break
                        if tp2_t and be_t and be_done:
                            send_telegram(f"⚠️ TP2+BE {name} {trade.get('trade_id')} Check broker")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP2+BE UNVERIFIED","entry":entry}); atomic_move(trade,"TP2+BE UNVERIFIED"); trade_done=True; break
                        if tp2_t:
                            send_telegram(f"🎉 TP2 HIT {name} {trade.get('trade_id')} Reply WIN")
                            PRICE_LEVEL_WINS[name]=PRICE_LEVEL_WINS.get(name,0)+1
                            PRICE_OUTCOMES.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"PRICE TP2","r":1.15,"source":"YAHOO PRICE-LEVEL ONLY"})
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TP2 +1.15R","entry":entry,"r":1.15}); atomic_move(trade,"TP2 +1.15R"); trade_done=True; break
                        if be_t and be_done:
                            send_telegram(f"⚠️ BACK TO ENTRY {name} {trade.get('trade_id')} Reply WIN/LOSS")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"BE AFTER TP1","entry":entry}); atomic_move(trade,"BE AFTER TP1"); trade_done=True; break
                        if sl_t:
                            send_telegram(f"⚠️ SL HIT {name} {trade.get('trade_id')} Check broker - BE={be_done}")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"SL AFTER TP1","entry":entry}); atomic_move(trade,"SL AFTER TP1"); trade_done=True; break
                        if orig_sl_t and be_done:
                            send_telegram(f"⚠️ ORIG SL AFTER TP1 {name} {trade.get('trade_id')} Check broker - Did BE get rejected?")
                            OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"ORIGINAL SL AFTER TP1 UNVERIFIED","entry":entry}); atomic_move(trade,"ORIGINAL SL AFTER TP1 UNVERIFIED"); trade_done=True; break
                        trade["last_processed"]=idx.timestamp() if hasattr(idx,'timestamp') else time.time(); LAST_PROCESSED_1M[name]=trade["last_processed"]
                if not trade_done:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20 and not trade.get("tp1_hit"):
                        send_telegram(f"⏰ TIMEOUT {name} {trade.get('trade_id')} Reply WIN/LOSS")
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"TIMEOUT","entry":trade["entry"]}); atomic_move(trade,"TIMEOUT")
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade.get('trade_id'),"result":"WATCH EXPIRED","entry":trade["entry"]}); atomic_move(trade,"WATCH EXPIRED")
                    else: remaining.append(trade)
        except Exception as e: print(f"Check error {e}",flush=True); remaining.append(trade)
    ACTIVE_TRADES=remaining; PENDING_USER_REPORTS=new_pending; save_outcomes()

def analyze(name,ysym,now):
    df_4h=get_data(ysym,"4h","60d"); df_1h=get_data(ysym,"1h","10d"); df_15m=get_data(ysym,"15m","10d"); df_5m=get_data(ysym,"5m","2d"); df_1m=get_data(ysym,"1m","1d"); df_1d=get_data(ysym,"1d","60d"); df_1w=get_data(ysym,"1wk","6mo")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None: return {"fail":True,"score":0,"reason":"No data"}
    if len(df_5m)<50 or len(df_15m)<50 or len(df_1h)<20 or len(df_4h)<20: return {"fail":True,"score":0,"reason":"Not enough candles"}
    df_4h=df_4h.iloc[:-1]; df_1h=df_1h.iloc[:-1]; df_15m=df_15m.iloc[:-1]; df_5m=df_5m.iloc[:-1]
    if df_1m is not None and len(df_1m)>1: df_1m=df_1m.iloc[:-1]
    elif df_1m is not None and len(df_1m)==1: df_1m=df_1m.iloc[0:0]
    c15=df_15m['Close'].tolist(); c5=df_5m['Close'].tolist(); c1=df_1h['Close'].tolist(); c4=df_4h['Close'].tolist()
    h5=df_5m['High'].tolist(); l5=df_5m['Low'].tolist(); h15=df_15m['High'].tolist(); l15=df_15m['Low'].tolist()
    v15=df_15m['Volume'].tolist() if 'Volume' in df_15m.columns else None
    score=0; details=[]; warnings=[]; smart_notes=[]; tags=[]; trend_strength=50; trend_label="NORMAL"
    e50_4h=ema(c4,50); e20_4h=ema(c4,20); p4=c4[-1]
    htf4="BUY" if p4>e50_4h and p4>e20_4h else ("SELL" if p4<e50_4h and p4<e20_4h else None)
    e50_1h=ema(c1,50); e20_1h=ema(c1,20); p1=c1[-1]
    htf="BUY" if p1>e50_1h and p1>e20_1h else ("SELL" if p1<e50_1h and p1<e20_1h else None)
    e50=ema(c15,50); e200=ema(c15,200); price=c15[-1]; trend=None
    if price>e50 and price>e200: trend="BUY"; score+=20; details.append("EMA 50+200 BUY +20")
    elif price<e50 and price<e200: trend="SELL"; score+=20; details.append("EMA 50+200 SELL +20")
    else: return {"fail":True,"score":score,"reason":f"EMA fail {price:.5f} vs {e50:.5f}/{e200:.5f}","trend":"NONE"}
    adx_val,plus_di,minus_di=adx_calc_true_wilder(h15,l15,c15,14)
    if adx_val>=25: score+=5; details.append(f"ADX {adx_val} STRONG +5"); tags.append(f"ADX STRONG {adx_val}"); smart_notes.append(f"ADX {adx_val} Strong")
    elif adx_val>=20: details.append(f"ADX {adx_val} Normal"); tags.append(f"ADX {adx_val}")
    else: details.append(f"ADX {adx_val} Weak"); tags.append(f"ADX WEAK {adx_val}")
    try:
        dist_pct=abs(price-e50)/price*100 if price!=0 else 0
        ema_series=[]; tmp=c15[0]; k=2/(50+1)
        for v in c15: tmp=v*k+tmp*(1-k); ema_series.append(tmp)
        slope=(ema_series[-1]-ema_series[-5])/ema_series[-5]*100 if len(ema_series)>=6 and ema_series[-5]!=0 else 0
        slope_val=slope if trend=="BUY" else -slope
        trend_strength=min(100,max(0,50+dist_pct*800+slope_val*800))
        if trend_strength>=75: trend_label="STRONG"; score+=5; details.append(f"TREND STRONG {trend_strength:.0f}% +5"); tags.append(f"TREND STRONG {trend_strength:.0f}%"); smart_notes.append("Strong Trend")
        elif trend_strength>=55: trend_label="NORMAL"; details.append(f"Trend Normal {trend_strength:.0f}%"); tags.append(f"TREND NORMAL {trend_strength:.0f}%")
        else: trend_label="WEAK"; details.append(f"Trend Weak {trend_strength:.0f}%"); tags.append(f"TREND WEAK {trend_strength:.0f}%")
    except Exception as e: details.append(f"Trend Error {e}")
    if htf is not None and htf==trend: score+=5; details.append(f"1H Agrees {htf} +5"); tags.append("1H AGREE")
    elif htf is not None and htf!=trend: details.append(f"1H Conflict {htf}"); tags.append("1H CONFLICT"); warnings.append(f"1H vs 15m {htf}")
    if htf4 is not None and htf4==trend: score+=5; details.append(f"4H Agrees {htf4} +5"); tags.append("4H AGREE"); smart_notes.append("4H Agree")
    elif htf4 is not None and htf4!=trend: details.append(f"4H Conflict {htf4}"); tags.append("4H CONFLICT"); warnings.append(f"4H vs 15m {htf4}")
    daily_label="None"; weekly_label="None"
    try:
        if df_1d is not None and len(df_1d)>=2:
            try:
                last_daily_ts=df_1d.index[-1]
                last_daily_utc_date=last_daily_ts.tz_convert('UTC').date() if hasattr(last_daily_ts,'tz_convert') else last_daily_ts.date()
                today_utc_date=dt.now(pytz.UTC).date(); use_idx=-2 if last_daily_utc_date==today_utc_date else -1
            except: use_idx=-2
            d_high=float(df_1d['High'].iloc[use_idx]); d_low=float(df_1d['Low'].iloc[use_idx])
            if trend=="BUY" and price>d_high: daily_label="ABOVE DAILY HIGH"; details.append("DAILY Above High (tag)"); tags.append("ABOVE DAILY HIGH"); smart_notes.append("Above Daily High")
            elif trend=="SELL" and price<d_low: daily_label="BELOW DAILY LOW"; details.append("DAILY Below Low (tag)"); tags.append("BELOW DAILY LOW"); smart_notes.append("Below Daily Low")
            else: daily_label="INSIDE DAILY RANGE"; details.append("Daily: Inside Range"); tags.append("INSIDE DAILY RANGE")
        if df_1w is not None and len(df_1w)>=2:
            try:
                last_wk_ts=df_1w.index[-1]; wk_start=last_wk_ts.date(); monday_this_week=now.date()-timedelta(days=now.weekday())
                use_w=-2 if wk_start==monday_this_week else -1
                if len(df_1w)<3 and use_w==-2: use_w=-1
            except: use_w=-2
            w_high=float(df_1w['High'].iloc[use_w]); w_low=float(df_1w['Low'].iloc[use_w])
            if trend=="BUY" and price>w_high: weekly_label="ABOVE WEEKLY HIGH"; tags.append("ABOVE WEEKLY HIGH"); details.append("Weekly: Above High")
            elif trend=="SELL" and price<w_low: weekly_label="BELOW WEEKLY LOW"; tags.append("BELOW WEEKLY LOW"); details.append("Weekly: Below Low")
            else: weekly_label="INSIDE WEEKLY"; details.append("Weekly: Inside Range")
    except Exception as e: details.append(f"Daily/Weekly Error {e}")
    wins=USER_REPORTED_WINS.get(name,0); losses=USER_REPORTED_LOSSES.get(name,0); pwins=PRICE_LEVEL_WINS.get(name,0); plosses=PRICE_LEVEL_LOSSES.get(name,0); hot_score=wins-losses
    if wins>=2 and wins>losses: score+=5; details.append(f"HOT PAIR {name} +5"); tags.append(f"HOT PAIR {wins}W {losses}L"); smart_notes.append("Hot Pair")
    elif losses>=2 and losses>wins: details.append(f"COLD PAIR {name}"); tags.append(f"COLD PAIR {wins}W {losses}L")
    else: details.append(f"Pair User: {wins}W {losses}L | Price: {pwins}W {plosses}L")
    sess_label,is_prime=get_session_label(now)
    if is_prime: score+=5; details.append(f"{sess_label} +5 - PRIME"); tags.append("PRIME SESSION"); smart_notes.append(sess_label)
    else: details.append(f"{sess_label} - Tag only"); tags.append(sess_label); smart_notes.append(sess_label)
    r=rsi_calc_wilder(c15,14)
    if trend=="BUY" and r>80: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    if trend=="SELL" and r<20: return {"fail":True,"score":score,"reason":f"RSI {r:.1f} extreme","trend":trend,"details":details}
    details.append(f"RSI {r:.1f}")
    if r>70 and trend=="BUY": tags.append("REVERSAL RISK - RSI High")
    if r<30 and trend=="SELL": tags.append("REVERSAL RISK - RSI Low")
    atr_val=atr_calc_wilder(h5,l5,c5,14)
    if atr_val<=0: atr_val=abs(price*0.001)
    atr_ratio=atr_val/price if price!=0 else 0.001
    if atr_ratio>0.0015: threshold=78; tags.append("HIGH MOMENTUM"); details.append("Adaptive Threshold 78 - Volatile")
    elif atr_ratio<0.0008: threshold=82; tags.append("LOW VOLUME"); details.append("Adaptive Threshold 82 - Quiet")
    else: threshold=80; tags.append("NORMAL MOMENTUM"); details.append("Adaptive Threshold 80 - Normal")
    vol_warning="Check broker spread"
    try:
        last_range=h5[-1]-l5[-1]
        if atr_val>0 and last_range/atr_val>1.2: vol_warning="HIGH VOLATILITY - Check broker spread"; tags.append("HIGH VOLATILITY"); details.append("Volatility: High")
        else: vol_warning="Volatility Normal - Still check broker spread"; details.append("Volatility: Normal")
    except: pass
    last_8_range=max(h15[-8:])-min(l15[-8:])
    is_chop=last_8_range/price<0.004
    if is_chop: tags.append("CHOP - Tight Range"); details.append(f"CHOP Tag - Range {last_8_range:.5f}"); smart_notes.append("Chop")
    else: details.append(f"Chop OK - Range {last_8_range:.5f}")
    if name=="XAUUSD" and atr_val>6.0: return {"fail":True,"score":score,"reason":f"Gold ATR too high {atr_val:.2f}","trend":trend,"details":details}
    last3=c5[-1]-c5[-4]; chase=False
    if trend=="BUY" and last3>atr_val*0.6: chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT"); tags.append("CHASE - Use Limit")
    elif trend=="SELL" and last3<-atr_val*0.6: chase=True; warnings.append("CHASE"); details.append("CHASE - Use Limit")
    else: details.append("Anti-Chase OK")
    big=0
    for i in range(-4,-1):
        rng=h5[i]-l5[i]
        if rng>atr_val*0.9:
            if trend=="BUY" and c5[i]>c5[i-1]: big+=1
            if trend=="SELL" and c5[i]<c5[i-1]: big+=1
    if big>=3: warnings.append("EXHAUSTED"); details.append("EXHAUSTION"); tags.append("EXHAUSTED")
    hi_prev=max(h15[-20:-1]); lo_prev=min(l15[-20:-1])
    bos_ok=(trend=="BUY" and c15[-1]>hi_prev) or (trend=="SELL" and c15[-1]<lo_prev)
    if bos_ok: score+=20; details.append("15m BOS +20")
    else: details.append("BOS 0")
    vol_label="No Volume Data"; avg_vol_for_veto=0; last_vol_for_veto=0
    try:
        if v15 is not None and len(v15)>=20:
            avg_vol=sum(v15[-20:-1])/19; last_vol=v15[-1]; avg_vol_for_veto=avg_vol; last_vol_for_veto=last_vol
            if avg_vol>0 and last_vol>avg_vol*1.5: vol_label="HIGH VOLUME"; score+=5; details.append(f"VOLUME HIGH {last_vol:.0f} vs {avg_vol:.0f} +5"); tags.append("HIGH VOLUME"); smart_notes.append("High Volume")
            elif avg_vol>0 and last_vol<avg_vol*0.5: vol_label="LOW VOLUME"; details.append(f"Volume Low {last_vol:.0f} vs {avg_vol:.0f}"); tags.append("LOW VOLUME")
            else: vol_label="NORMAL VOLUME"; details.append(f"Volume Normal {last_vol:.0f}")
    except Exception as e: details.append(f"Volume Error {e}")
    structure_tag="None"
    try:
        last_highs=h15[-20:]; last_lows=l15[-20:]; max_h=max(last_highs); min_l=min(last_lows)
        touches_high=sum(1 for h in last_highs if abs(h-max_h)/max_h<0.001); touches_low=sum(1 for l in last_lows if abs(l-min_l)/min_l<0.001)
        if trend=="SELL" and touches_high>=2: structure_tag="DOUBLE TOP"; tags.append("DOUBLE TOP"); details.append("Structure: Double Top"); smart_notes.append("Double Top")
        elif trend=="BUY" and touches_low>=2: structure_tag="DOUBLE BOTTOM"; tags.append("DOUBLE BOTTOM"); details.append("Structure: Double Bottom"); smart_notes.append("Double Bottom")
        elif bos_ok: structure_tag="CLEAN BREAKOUT"; score+=5; details.append("CLEAN BREAKOUT +5"); tags.append("CLEAN BREAKOUT"); smart_notes.append("Clean Breakout")
        else: details.append("Structure: Normal")
    except Exception as e: details.append(f"Structure Error {e}")
    prev_low=min(l5[-20:-2]); prev_high=max(h5[-20:-2]); sweep_ok=False; sweep_bonus=False; sweep_level="Local"; y_high=None; y_low=None
    if df_1d is not None and len(df_1d)>=2:
        try: y_high=float(df_1d['High'].iloc[-2]); y_low=float(df_1d['Low'].iloc[-2])
        except: pass
    asia_high=max(h5[-40:-20]) if len(h5)>40 else None; asia_low=min(l5[-40:-20]) if len(l5)>40 else None
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
        score+=15; details.append(f"Sweep +15 ({sweep_level})"); tags.append(f"LIQUIDITY SWEEP {sweep_level}")
        if sweep_bonus: score+=15; details.append("SMART Sweep +15"); smart_notes.append(f"Sweep {sweep_level}")
    else: details.append("Sweep 0")
    bos5=False
    for i in range(len(df_5m)-5,len(df_5m)-1):
        if trend=="BUY" and c5[i]>max(h5[i-5:i]): bos5=True
        if trend=="SELL" and c5[i]<min(l5[i-5:i]): bos5=True
    if bos5: score+=10; details.append("5m BOS +10")
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
        if dist_pct>0.003: fvg_distance_ok=False; details.append(f"FVG FAR {dist_pct*100:.2f}% - Does NOT count"); tags.append("FVG FAR - Does NOT count"); fvg_ok=False
        elif fvg_bottom<=current<=fvg_top: fvg_inside=True; score+=20; details.append("FVG INSIDE FRESH +20"); fvg_ok=True
        else: score+=10; details.append("FVG Near Fresh +10"); fvg_ok=True
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
            if m1_ok: score+=10; details.append("1m Rejection CONFIRM +10"); smart_notes.append("1m Confirm")
            else: details.append("1m No confirm")
        except: details.append("1m Error")
    repeat_count=PAIR_SIGNAL_COUNT.get(name,0)
    if repeat_count>=2: tags.append(f"REPEAT - {repeat_count}th signal today")
    freshness=100; freshness_label="FRESH"
    try:
        last_time=PAIR_LAST_SIGNAL_TIME.get(name)
        if last_time:
            mins_ago=(time.time()-last_time)/60
            if mins_ago<5: freshness=100; freshness_label="FRESH"
            elif mins_ago<15: freshness=80; freshness_label="FRESH"
            elif mins_ago<30: freshness=60; freshness_label="STALE"
            else: freshness=100; freshness_label="FRESH - New cycle"
            tags.append(f"{freshness_label} - Last {mins_ago:.0f}m ago"); details.append(f"Freshness: {freshness_label} ({mins_ago:.0f}m ago)")
        else: tags.append("FRESH - First today"); details.append("Freshness: FRESH - First today")
    except: pass
    raw_score=score; score=min(score,120); score_pct=round((score/120)*100,1)
    print(f"{name} {trend} Score {score}/120 Raw {raw_score} Thr {threshold} RSI {r:.1f} ADX {adx_val} OB {ob_type} FVG {fvg_fresh} 1m {m1_ok} Trend {trend_label} {trend_strength:.0f}% User {wins}W {losses}L",flush=True)
    if score<threshold-10: return {"fail":True,"score":score,"reason":f"Score {score} < Watch {threshold-10}","trend":trend,"details":details,"threshold":threshold}
    if score<threshold: return {"watch":True,"score":score,"trend":trend,"details":details,"symbol":name,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"smart_notes":smart_notes,"ob_type":ob_type,"tags":tags,"threshold":threshold,"sess_label":sess_label,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label,"adx":adx_val,"vol_label":vol_label,"daily_label":daily_label,"weekly_label":weekly_label,"score_pct":score_pct}
    conf=sum([1 for x in [bos_ok,sweep_ok,fvg_ok,ob_ok] if x])
    if score>=90 and conf>=3 and not chase and (fvg_inside or ob_inside) and rej and m1_ok: tier="S-TIER SCALP"; tier_label="S"; take_it="YES - BEST SIGNAL"
    elif score>=80 and conf>=3 and not chase: tier="A-TIER SCALP"; tier_label="A"; take_it="YES - SCALP 0.01 only"
    elif score>=threshold and conf>=2: tier="B-TIER SCALP"; tier_label="B"; take_it="CAUTION - Smaller / Skip if unsure"
    else: return {"fail":True,"score":score,"reason":f"Quality Gate FAIL - Need 2/4, got {conf}/4","trend":trend,"details":details,"threshold":threshold}
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
    # V37.1 FIX 5: Use SL_ATR_MULT only
    fvg_for_sl=fvg_found and fvg_distance_ok
    if trend=="BUY":
        struct_sl = (ob_bottom - atr_val*0.2) if ob_ok else ((fvg_bottom - atr_val*0.2) if fvg_for_sl else None)
        atr_sl = entry - atr_val*SL_ATR_MULT
        if struct_sl is not None: sl = min(struct_sl, atr_sl)
        else: sl = atr_sl
        risk = entry - sl
    else:
        struct_sl = (ob_top + atr_val*0.2) if ob_ok else ((fvg_top + atr_val*0.2) if fvg_for_sl else None)
        atr_sl = entry + atr_val*SL_ATR_MULT
        if struct_sl is not None: sl = max(struct_sl, atr_sl)
        else: sl = atr_sl
        risk = sl - entry
    if risk<=0: return {"fail":True,"score":score,"reason":"Risk <=0","trend":trend}
    # V37.1 FIX 6: Yahoo proxy buffer for XAUUSD/US30
    if name in YAHOO_PROXY_PAIRS:
        risk = risk * 1.2
        if trend=="BUY":
            sl = entry - risk; tp1 = entry + risk*0.8; tp2 = entry + risk*1.5
        else:
            sl = entry + risk; tp1 = entry - risk*0.8; tp2 = entry - risk*1.5
    max_r=MAX_RISK.get(name,9999)
    if risk>max_r: return {"fail":True,"score":score,"reason":f"SL Cap {risk:.5f} > {max_r}","trend":trend,"details":details}
    if risk/entry>0.01: return {"fail":True,"score":score,"reason":"SL too wide","trend":trend,"details":details}
    if trend=="BUY": tp1=entry+risk*0.8; tp2=entry+risk*1.5
    else: tp1=entry-risk*0.8; tp2=entry-risk*1.5
    min_sl = MIN_SL.get(name, 0.00040)
    if risk < min_sl:
        return {"fail":True,"score":score,"reason":f"VETO SL too small {risk:.5f} < {min_sl}","trend":trend,"details":details}
    if risk/entry > 0.01:
        return {"fail":True,"score":score,"reason":"VETO SL expanded > 1%","trend":trend,"details":details}
    if risk > max_r:
        return {"fail":True,"score":score,"reason":"VETO SL expanded > cap","trend":trend,"details":details}
    if adx_val < 20:
        return {"fail":True,"score":score,"reason":f"VETO ADX weak {adx_val}","trend":trend,"details":details}
    if is_chop:
        return {"fail":True,"score":score,"reason":"VETO CHOP","trend":trend,"details":details}
    if repeat_count >= 2:
        return {"fail":True,"score":score,"reason":f"VETO Repeat {repeat_count}th","trend":trend,"details":details}
    if avg_vol_for_veto>0 and last_vol_for_veto < avg_vol_for_veto*0.8:
        return {"fail":True,"score":score,"reason":"VETO Low Volume","trend":trend,"details":details}
    rr = abs(tp2-entry)/risk if risk>0 else 0
    if rr < 1.5:
        return {"fail":True,"score":score,"reason":f"VETO R:R {rr:.2f} < 1.5","trend":trend,"details":details}
    risk_dist=round(float(risk),5)
    return {"symbol":name,"trend":trend,"order_type":order_type,"entry":round(float(entry),5),"tech_entry":round(float(tech_entry),5),"monitor_entry":round(float(monitor_entry),5),"sl":round(float(sl),5),"tp1":round(float(tp1),5),"tp2":round(float(tp2),5),"score":score,"score_pct":score_pct,"details":details,"tier":tier,"tier_label":tier_label,"take_it":take_it,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"entry_type":entry_type,"ob_type":ob_type,"ob_quality":ob_quality,"ob_top":round(float(ob_top),5) if ob_ok else 0,"ob_bottom":round(float(ob_bottom),5) if ob_ok else 0,"warnings":warnings,"chase":chase,"fvg_inside":fvg_inside,"fvg_fresh":fvg_fresh,"fvg_distance_ok":fvg_distance_ok,"rejection":rej,"inval":round(float(sl),5),"smart_notes":smart_notes,"sl_mult":SL_ATR_MULT,"sweep_level":sweep_level if sweep_ok else "None","m1_ok":m1_ok,"tags":tags,"threshold":threshold,"sess_label":sess_label,"is_chop":is_chop,"is_prime":is_prime,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label,"freshness_pct":freshness,"hot_score":hot_score,"wins":wins,"losses":losses,"pwins":pwins,"plosses":plosses,"adx":adx_val,"vol_label":vol_label,"daily_label":daily_label,"weekly_label":weekly_label,"vol_warning":vol_warning,"risk_dist":risk_dist}

def bot_loop():
    load_outcomes()
    send_telegram("V37.1 LIVE - Clean\n12 Pairs | Vetoes ON | Max 2/pair/day\n⚠️ XAUUSD/US30 Yahoo proxy - CHECK BROKER PRICE\nCONFIRM T001 / BE T001 / WIN T001 / LOSS T001")
    sent=set(); last_hunt=time.time(); last_sent_time={}; last_debug=time.time(); debug_reasons={}
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz); hour=now.hour+now.minute/60.0
            maybe_reset_daily(now); poll_telegram_commands(); cleanup_pending_reports(); check_active_trades(now)
            if now.weekday()>=5:
                time.sleep(60); continue
            if not (9.0<=hour<21.0): time.sleep(60); continue
            if is_news_time(now): time.sleep(300); continue
            sess_label,is_prime=get_session_label(now)
            print(f"V37.1 checking {now} Session {sess_label} Active {len(ACTIVE_TRADES)}",flush=True)
            found=0; best_score=0; best_pair="None"
            for name,ysym in SYMBOLS.items():
                try:
                    if name in PAIR_COOLDOWN_UNTIL and time.time()<PAIR_COOLDOWN_UNTIL[name]: continue
                    if name=="XAUUSD" and not (11.0<=hour<19.0): continue
                    if any(t["pair"]==name for t in ACTIVE_TRADES): continue
                    res=analyze(name,ysym,now)
                    if res and res.get("watch"):
                        continue
                    if res and res.get("fail"):
                        reason=res.get("reason","")
                        if reason.startswith("VETO"):
                            VETO_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trend":res.get("trend"),"score":res.get("score",0),"reason":reason})
                            save_outcomes()
                        continue
                    if res and not res.get("fail"):
                        found+=1; last_hunt=time.time(); cool=f"{name}-{res['trend']}-{res['tier_label']}"
                        if cool in last_sent_time and (time.time()-last_sent_time[cool])<1200: continue
                        key=f"{name}-{res['trend']}-{res['entry']}-{res['tier_label']}"
                        if key in sent: continue
                        trade_id=new_trade_id()
                        # V37.1 FIX 6: Yahoo warning in signal
                        yahoo_warn = "\n⚠️ YAHOO PROXY - CHECK BROKER PRICE!" if res['symbol'] in YAHOO_PROXY_PAIRS else ""
                        msg=f"SIGNAL - ID {trade_id}\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder: {res['order_type']}\nEntry: {res['entry']}\nSL: {res['sl']} (ATR x {SL_ATR_MULT})\nTP1: {res['tp1']}\nTP2: {res['tp2']}\nScore: {res['score']}/120 ({res.get('score_pct')}%)\nReason: {res.get('structure_tag','')} + {res.get('sess_label','')}{yahoo_warn}\nReply: CONFIRM {trade_id}"
                        delivered=send_telegram(msg)
                        if not delivered: continue
                        PAIR_SIGNAL_COUNT[name]=PAIR_SIGNAL_COUNT.get(name,0)+1; PAIR_LAST_SIGNAL_TIME[name]=time.time()
                        sent.add(key); last_sent_time[cool]=time.time()
                        OUTCOME_LOG.append({"time":now.strftime("%Y-%m-%d %H:%M"),"pair":name,"trade_id":trade_id,"tier":res['tier_label'],"trend":res['trend'],"score":res['score'],"entry":res['entry'],"sl":res['sl'],"tp1":res['tp1'],"tp2":res['tp2'],"status":"PENDING_CONFIRM"})
                        ACTIVE_TRADES.append({"pair":name,"trade_id":trade_id,"trend":res['trend'],"entry":res['entry'],"tech_entry":res.get('tech_entry'),"sl":res['sl'],"tp1":res['tp1'],"tp2":res['tp2'],"time":time.time(),"tier":res['tier_label'],"tp1_hit":False,"be_done":False,"status":"PENDING_CONFIRM","effective_sl":res['sl'],"last_processed":time.time(),"live_price":res.get('live_price'),"order_type":res.get('order_type')})
                        save_outcomes()
                    else:
                        sc=res.get("score",0) if res else 0
                        if sc>best_score: best_score=sc; best_pair=name
                except Exception as e: print(f"Error {name}: {e}",flush=True); traceback.print_exc()
            if len(sent)>200: sent.clear()
            gc.collect(); time.sleep(60)
        except Exception as e: print(f"Loop crash {e}",flush=True); traceback.print_exc(); time.sleep(10)
if __name__=="__main__":
    threading.Thread(target=heartbeat,daemon=True).start()
    threading.Thread(target=bot_loop,daemon=True).start()
    print("V37.1 - Flask MAIN",flush=True); run_web()
