import os, time, requests, threading, pytz, gc, traceback, json, random
from datetime import datetime as dt
import yfinance as yf
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "V36.5 RELIABILITY ALIVE", 200
@app.route('/health')
def health():
    return "OK", 200
@app.route('/<path:path>')
def catch_all(path):
    return "V36.5 RELIABILITY ALIVE", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"V36.5 Flask on {port}", flush=True)
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
# V36.5: RENAMED - USER-REPORTED not Broker-Verified (honest)
PRICE_LEVEL_WINS = {}
PRICE_LEVEL_LOSSES = {}
USER_REPORTED_WINS = {}
USER_REPORTED_LOSSES = {}
PAIR_LAST_SIGNAL_TIME = {}
OUTCOME_LOG = []
ACTIVE_TRADES = []
TRADE_HISTORY_FILE = "/tmp/trade_history.json"
TELEGRAM_OFFSET = 0
LAST_PROCESSED_1M = {}
TRADE_COUNTER = 0
LAST_RESET_DATE = ""
COMPLETED_TRADE_IDS = set()  # V36.5: Prevent duplicate WIN/LOSS

def save_outcomes():
    try:
        with open(TRADE_HISTORY_FILE, "w") as f:
            json.dump({"price_wins": PRICE_LEVEL_WINS, "price_losses": PRICE_LEVEL_LOSSES, "user_wins": USER_REPORTED_WINS, "user_losses": USER_REPORTED_LOSSES, "outcomes": OUTCOME_LOG[-100:], "active": ACTIVE_TRADES, "offset": TELEGRAM_OFFSET, "last_processed": LAST_PROCESSED_1M, "trade_counter": TRADE_COUNTER, "completed_ids": list(COMPLETED_TRADE_IDS), "last_reset": LAST_RESET_DATE, "signal_count": PAIR_SIGNAL_COUNT}, f)
    except Exception as e:
        print(f"Save error {e}", flush=True)

def load_outcomes():
    global PRICE_LEVEL_WINS, PRICE_LEVEL_LOSSES, USER_REPORTED_WINS, USER_REPORTED_LOSSES, OUTCOME_LOG, ACTIVE_TRADES, TELEGRAM_OFFSET, LAST_PROCESSED_1M, TRADE_COUNTER, COMPLETED_TRADE_IDS, LAST_RESET_DATE, PAIR_SIGNAL_COUNT
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE, "r") as f:
                d = json.load(f)
                PRICE_LEVEL_WINS = d.get("price_wins", {})
                PRICE_LEVEL_LOSSES = d.get("price_losses", {})
                USER_REPORTED_WINS = d.get("user_wins", {})
                USER_REPORTED_LOSSES = d.get("user_losses", {})
                OUTCOME_LOG = d.get("outcomes", [])
                ACTIVE_TRADES = d.get("active", [])
                TELEGRAM_OFFSET = d.get("offset", 0)
                LAST_PROCESSED_1M = d.get("last_processed", {})
                TRADE_COUNTER = d.get("trade_counter", 0)
                COMPLETED_TRADE_IDS = set(d.get("completed_ids", []))
                LAST_RESET_DATE = d.get("last_reset", "")
                PAIR_SIGNAL_COUNT = d.get("signal_count", {})
                print(f"Loaded: Price {PRICE_LEVEL_WINS}/{PRICE_LEVEL_LOSSES} User {USER_REPORTED_WINS}/{USER_REPORTED_LOSSES} Active {len(ACTIVE_TRADES)} Counter {TRADE_COUNTER}", flush=True)
    except Exception as e:
        print(f"Load error {e}", flush=True)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        # V36.5 FIX: Check JSON ok field, not just HTTP 200
        try:
            j = r.json()
            ok = j.get("ok", False)
            print(f"Telegram {r.status_code} ok={ok}", flush=True)
            return r.status_code == 200 and ok
        except:
            return r.status_code == 200
    except Exception as e:
        print(f"Telegram error {e}", flush=True); return False

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

def rsi_calc_wilder(values, period=14):
    if len(values) < period + 1: return 50
    gains=[]; losses=[]
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0)); losses.append(max(-diff, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if ag == 0 and al == 0: return 50
    if al == 0: return 100
    rs = ag / al
    return 100 - (100 / (1 + rs))

def atr_calc_wilder(highs, lows, closes, period=14):
    if len(closes) < period + 1: return abs(closes[-1] * 0.001)
    trs=[]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period-1) + tr) / period
    return atr

def adx_calc_true_wilder(highs, lows, closes, period=14):
    try:
        if len(closes) < period*3 + 1: return 20, 0, 0
        trs=[]; plus_dm=[]; minus_dm=[]
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
            up = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            plus_dm.append(up if (up > down and up > 0) else 0)
            minus_dm.append(down if (down > up and down > 0) else 0)
        atr = sum(trs[:period])
        p_dm = sum(plus_dm[:period])
        m_dm = sum(minus_dm[:period])
        dx_series = []
        for i in range(period, len(trs)):
            atr = atr - (atr/period) + trs[i]
            p_dm = p_dm - (p_dm/period) + plus_dm[i]
            m_dm = m_dm - (m_dm/period) + minus_dm[i]
            if atr == 0: continue
            plus_di = 100 * p_dm / atr
            minus_di = 100 * m_dm / atr
            denom = plus_di + minus_di
            dx = 100 * abs(plus_di - minus_di) / denom if denom != 0 else 0
            dx_series.append(dx)
        if len(dx_series) < period: return round(float(dx_series[-1]) if dx_series else 20,1), 0, 0
        adx = sum(dx_series[:period]) / period
        for dx in dx_series[period:]:
            adx = (adx * (period-1) + dx) / period
        if atr == 0: return round(float(adx),1), 0, 0
        plus_di = 100 * p_dm / atr
        minus_di = 100 * m_dm / atr
        return round(float(adx),1), round(float(plus_di),1), round(float(minus_di),1)
    except Exception as e:
        print(f"ADX error {e}", flush=True); return 20, 0, 0

def get_data(sym, interval, period):
    try:
        df = yf.download(sym, interval=interval, period=period, progress=False, auto_adjust=False, threads=False, timeout=20)
        if df.empty: return None
        if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"yf error {sym} {interval} {e}", flush=True); return None

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
                    if broken and trend=="SELL": obs.append({"type":"BREAKER_BEAR","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="BUY":
                        taps=sum(1 for k in range(j+1,n) if bottom <= lows[k] <= top)
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
                    if broken and trend=="BUY": obs.append({"type":"BREAKER_BULL","top":top,"bottom":bottom,"mid":(top+bottom)/2,"fresh":(n-j)<12})
                    elif not broken and trend=="SELL":
                        taps=sum(1 for k in range(j+1,n) if bottom <= highs[k] <= top)
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

# V36.5 NEW: Daily reset + Trade ID generator
def maybe_reset_daily(now):
    global PAIR_SIGNAL_COUNT, LAST_RESET_DATE
    today = now.strftime("%Y-%m-%d")
    if LAST_RESET_DATE != today:
        PAIR_SIGNAL_COUNT = {}
        LAST_RESET_DATE = today
        save_outcomes()
        print(f"Daily reset {today}", flush=True)

def new_trade_id():
    global TRADE_COUNTER
    TRADE_COUNTER += 1
    save_outcomes()
    return f"T{TRADE_COUNTER:03d}"

# V36.5 FIX: Telegram security + Trade ID required + No duplicate WIN/LOSS
def poll_telegram_commands():
    global TELEGRAM_OFFSET, ACTIVE_TRADES
    if not TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        r = requests.get(url, params={"offset": TELEGRAM_OFFSET, "timeout": 5}, timeout=10)
        if r.status_code != 200: return
        data = r.json()
        for upd in data.get("result", []):
            TELEGRAM_OFFSET = upd["update_id"] + 1
            msg = upd.get("message", {})
            # V36.5 FIX 1: Restrict to YOUR chat ID only
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                print(f"Ignored command from unauthorized chat {chat_id}", flush=True)
                continue
            text = (msg.get("text") or "").strip().upper()
            if not text: continue
            print(f"Telegram command from authorized chat: {text}", flush=True)
            # Format: CONFIRM T001 / BE DONE T001 / WIN T001 / LOSS T001 / SKIP T001
            # Also accept old format CONFIRM GBPUSD for backwards compat, but prefer ID
            parts = text.split()
            cmd = parts[0]
            # Extract Trade ID if present (T001 etc) or Pair
            trade_id = None
            pair_arg = None
            for p in parts[1:]:
                if p.startswith("T") and len(p)>=4 and p[1:].isdigit():
                    trade_id = p
                elif p in SYMBOLS:
                    pair_arg = p
            def find_trade():
                if trade_id:
                    for t in ACTIVE_TRADES:
                        if t.get("trade_id")==trade_id:
                            return t
                    return None
                if pair_arg:
                    # If multiple trades same pair, pick newest pending
                    candidates = [t for t in ACTIVE_TRADES if t["pair"]==pair_arg]
                    if len(candidates)==1: return candidates[0]
                    if len(candidates)>1:
                        return candidates[-1]
                    return None
                return None
            if cmd=="CONFIRM":
                t = find_trade()
                if not t:
                    send_telegram(f"❌ CONFIRM FAILED - No active trade found for {trade_id or pair_arg or '?'}\n\nUse Trade ID from signal: CONFIRM T001\nCheck active trades. If signal expired, it's gone.")
                    continue
                if t.get("status")!="PENDING_CONFIRM":
                    send_telegram(f"⚠️ Already confirmed - {t['pair']} {t.get('trade_id','')}\nStatus: {t.get('status')}")
                    continue
                # V36.5 FIX 3: Set confirm_time AND last_processed = confirm_time (no old candles)
                t["status"]="PENDING_FILL"
                t["confirm_time"]=time.time()
                # V36.5: Initialize last_processed at confirmation, not 0
                LAST_PROCESSED_1M[t["pair"]]=time.time()
                # Also store confirm timestamp for candle filtering
                t["confirm_ts"]=time.time()
                send_telegram(f"✅ CONFIRMED by YOU - {t['pair']} {t['trend']} ID {t.get('trade_id')}\n\nYou confirmed you placed the order.\nBot now watches for PRICE TOUCH of Entry {t['entry']}.\nOnly candles AFTER your confirm time count. Old candles ignored.\nIf price never touches in 10m AFTER CONFIRM, bot expires it.\nThis is Price-Level Estimate, NOT Broker-Verified.\nID: {t.get('trade_id')} - Use this ID for BE DONE / WIN / LOSS")
                save_outcomes()
            elif cmd=="BE" or (cmd=="BE" and len(parts)>1) or text.startswith("BE DONE"):
                # BE DONE T001
                t = find_trade()
                if not t:
                    send_telegram(f"❌ BE DONE FAILED - No active trade {trade_id or pair_arg}")
                    continue
                if not t.get("tp1_hit"):
                    send_telegram(f"⚠️ BE DONE too early - {t['pair']} {t.get('trade_id')} TP1 not yet touched. Wait for TP1.")
                    continue
                t["be_confirmed"]=True
                t["effective_sl"]=t["entry"]
                send_telegram(f"✅ BE CONFIRMED by YOU - {t['pair']} ID {t.get('trade_id')}\n\nYou confirmed you moved SL to Entry {t['entry']} and closed 50%.\nBot now watches BE as real SL.\nThank you - This is USER-REPORTED BE.")
                save_outcomes()
            elif cmd=="WIN":
                # V36.5 FIX: Prevent duplicate, require trade ID or active trade
                if trade_id and trade_id in COMPLETED_TRADE_IDS:
                    send_telegram(f"❌ DUPLICATE WIN REJECTED - {trade_id} already recorded. No double count.")
                    continue
                t = find_trade()
                # If trade_id given but no active trade, check if already completed
                if trade_id and not t and trade_id in COMPLETED_TRADE_IDS:
                    send_telegram(f"❌ DUPLICATE WIN REJECTED - {trade_id} already recorded.")
                    continue
                if trade_id and not t:
                    send_telegram(f"❌ WIN FAILED - No active trade {trade_id}. Use correct ID. No win recorded.")
                    continue
                if not t:
                    send_telegram(f"❌ WIN FAILED - No active trade for {pair_arg or '?'}. Use WIN T001 with Trade ID.")
                    continue
                pair = t["pair"]
                tid = t.get("trade_id")
                USER_REPORTED_WINS[pair]=USER_REPORTED_WINS.get(pair,0)+1
                if tid: COMPLETED_TRADE_IDS.add(tid)
                ACTIVE_TRADES=[x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]
                OUTCOME_LOG.append({"time": dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"), "pair": pair, "trade_id": tid, "result": "USER-REPORTED WIN - User Confirmed (Not Broker API)", "r": 1.15})
                send_telegram(f"✅ USER-REPORTED WIN Recorded - {pair} ID {tid}\n\nYou reported WIN. This counts for Hot Pair.\nNote: This is USER-REPORTED, not Broker API verified. Bot never saw your broker account.\nPrice-Level wins do NOT count - Only YOUR reported wins count.")
                save_outcomes()
            elif cmd=="LOSS":
                if trade_id and trade_id in COMPLETED_TRADE_IDS:
                    send_telegram(f"❌ DUPLICATE LOSS REJECTED - {trade_id} already recorded.")
                    continue
                t = find_trade()
                if trade_id and not t:
                    send_telegram(f"❌ LOSS FAILED - No active trade {trade_id}.")
                    continue
                if not t:
                    send_telegram(f"❌ LOSS FAILED - No active trade for {pair_arg or '?'}. Use LOSS T001.")
                    continue
                pair = t["pair"]
                tid = t.get("trade_id")
                USER_REPORTED_LOSSES[pair]=USER_REPORTED_LOSSES.get(pair,0)+1
                if tid: COMPLETED_TRADE_IDS.add(tid)
                ACTIVE_TRADES=[x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]
                OUTCOME_LOG.append({"time": dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"), "pair": pair, "trade_id": tid, "result": "USER-REPORTED LOSS - User Confirmed", "r": -1.0})
                send_telegram(f"❌ USER-REPORTED LOSS Recorded - {pair} ID {tid}\n\nYou reported LOSS. This counts for Cold Pair.\nPair paused 2h.")
                PAIR_COOLDOWN_UNTIL[pair]=time.time()+7200
                save_outcomes()
            elif cmd=="SKIP" or text.startswith("NO TRADE"):
                t = find_trade()
                if not t:
                    send_telegram(f"❌ SKIP FAILED - No active trade {trade_id or pair_arg}")
                    continue
                tid=t.get("trade_id"); pair=t["pair"]
                ACTIVE_TRADES=[x for x in ACTIVE_TRADES if x.get("trade_id")!=tid]
                if tid: COMPLETED_TRADE_IDS.add(tid)
                OUTCOME_LOG.append({"time": dt.now(pytz.timezone("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M"), "pair": pair, "trade_id": tid, "result": "User Skipped - No Trade Placed"})
                send_telegram(f"⏭️ SKIPPED - {pair} ID {tid} - No trade placed. Bot stops tracking. No win/loss.")
                save_outcomes()
        save_outcomes()
    except Exception as e:
        print(f"Poll commands error {e}", flush=True)

# V36.5 FIX: Process entry-touch candle, expiry from confirm_time, BE+SL same candle, no old candles
def check_active_trades(now):
    global ACTIVE_TRADES
    if not ACTIVE_TRADES: return
    remaining=[]
    for trade in ACTIVE_TRADES:
        try:
            name=trade["pair"]; ysym=SYMBOLS.get(name)
            if not ysym: continue
            status=trade.get("status","PENDING_CONFIRM")
            if status=="PENDING_CONFIRM":
                age=(time.time()-trade["time"])/60
                if age>10:
                    send_telegram(f"❌ EXPIRED - No Confirm - {name} {trade['trend']} ID {trade.get('trade_id')}\n\nYou did not reply CONFIRM {trade.get('trade_id')} in 10m.\nBot did NOT track TP/SL. No win/loss.\nIf you did take it, reply WIN/LOSS later with ID to record user-reported result.")
                    OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "EXPIRED - No Confirm", "entry": trade["entry"]})
                    save_outcomes()
                else:
                    remaining.append(trade)
                continue
            if status=="PENDING_FILL":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                # V36.5 FIX 1: Only candles AFTER confirm_time
                confirm_ts = trade.get("confirm_ts", trade.get("confirm_time", trade["time"]))
                # V36.5 FIX: Use confirm_ts as floor, not 0
                last_ts = max(LAST_PROCESSED_1M.get(name, 0), confirm_ts - 60)  # allow 1m overlap for entry candle, but filter by confirm_ts
                touched=False; touch_candle_row=None; touch_idx=None
                for idx, row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    # V36.5: Skip candles before confirm (old candles)
                    if ts < confirm_ts - 5: continue
                    if ts <= LAST_PROCESSED_1M.get(name, confirm_ts-60) and ts < confirm_ts:
                        continue
                    # Only consider candles at/after confirm
                    if ts < confirm_ts: continue
                    low=float(row['Low']); high=float(row['High'])
                    if trade["trend"]=="BUY" and low<=trade["entry"]: touched=True; touch_candle_row=row; touch_idx=idx; break
                    if trade["trend"]=="SELL" and high>=trade["entry"]: touched=True; touch_candle_row=row; touch_idx=idx; break
                if touched:
                    trade["status"]="PRICE_TOUCHED"
                    trade["touch_time"]=time.time()
                    # V36.5 FIX 2: Do NOT advance LAST_PROCESSED past entry candle - keep it at entry candle so TP/SL can process it
                    if touch_idx is not None:
                        # Set to just before entry candle, so next cycle processes entry candle for TP/SL
                        LAST_PROCESSED_1M[name]=touch_idx.timestamp() - 1 if hasattr(touch_idx,'timestamp') else time.time()-60
                    send_telegram(f"🔔 PRICE TOUCHED — POSSIBLE FILL (NOT Confirmed) - {name} {trade['trend']} ID {trade.get('trade_id')}\n\nEntry: {trade['entry']} touched on Yahoo 1m AFTER your confirm.\nDid YOUR broker fill you? Bot now watches TP1/SL as Price-Level Estimate.\nIf you did NOT place it, reply SKIP {trade.get('trade_id')}.\nNOT Broker-Verified. Entry candle will also be checked for TP/SL.")
                    remaining.append(trade)
                else:
                    # V36.5 FIX 3: Expiry from confirm_time, not signal time
                    confirm_t = trade.get("confirm_time", trade["time"])
                    age=(time.time()-confirm_t)/60
                    # Update last processed to latest, but not before confirm
                    if len(df_completed)>0:
                        LAST_PROCESSED_1M[name]=df_completed.index[-1].timestamp() if hasattr(df_completed.index[-1],'timestamp') else time.time()
                    if age>10:
                        send_telegram(f"❌ LIMIT EXPIRED - {name} {trade['trend']} ID {trade.get('trade_id')}\n\nEntry {trade['entry']} never touched in 10m AFTER CONFIRM.\nCancel on broker if placed. No trade opened.")
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "EXPIRED - Never Touched After Confirm", "entry": trade["entry"]})
                        save_outcomes()
                    else: remaining.append(trade)
                save_outcomes()
                continue
            if status=="PRICE_TOUCHED":
                df=get_data(ysym,"1m","1d")
                if df is None or len(df)==0: remaining.append(trade); continue
                df_completed=df.iloc[:-1] if len(df)>1 else df
                # V36.5: Process from last_processed (which is entry candle -1), so entry candle included
                last_ts=LAST_PROCESSED_1M.get(name, trade.get("confirm_ts", time.time()-3600))
                candles_to_process=[]
                for idx, row in df_completed.iterrows():
                    ts=idx.timestamp() if hasattr(idx,'timestamp') else 0
                    if ts>last_ts:
                        candles_to_process.append((idx,row))
                if df_completed is not None and len(df_completed)>0:
                    # Will update after processing, not before
                    pass
                if not candles_to_process:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20 and not trade.get("tp1_hit"):
                        send_telegram(f"⏰ 20 MIN TIMEOUT AFTER PRICE TOUCH - {name} {trade['trend']} ID {trade.get('trade_id')}\n\nTP1 not touched in 20m. Exit on broker. Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TIMEOUT 20m", "entry": trade["entry"]})
                        save_outcomes()
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "WATCH EXPIRED 60m", "entry": trade["entry"]}); save_outcomes()
                    else: remaining.append(trade)
                    continue
                trade_done=False
                # Process candles in order
                for idx,row in candles_to_process:
                    if trade_done: break
                    high=float(row['High']); low=float(row['Low']); close=float(row['Close'])
                    entry=trade["entry"]; sl=trade["sl"]; tp1=trade["tp1"]; tp2=trade["tp2"]; trend=trade["trend"]
                    tp1_hit=trade.get("tp1_hit",False)
                    be_confirmed=trade.get("be_confirmed",False)
                    effective_sl=trade.get("effective_sl", sl)
                    if trend=="BUY":
                        tp1_t=high>=tp1; tp2_t=high>=tp2; sl_t=low<=effective_sl; be_t=low<=entry if tp1_hit else False
                        orig_sl_t=low<=sl
                        entry_t=low<=entry
                    else:
                        tp1_t=low<=tp1; tp2_t=low<=tp2; sl_t=high>=effective_sl; be_t=high>=entry if tp1_hit else False
                        orig_sl_t=high>=sl
                        entry_t=high>=entry
                    # V36.5 FIX: If this is entry candle and also TP/SL, handle ambiguity
                    is_entry_candle = (not tp1_hit and trade.get("status")=="PRICE_TOUCHED" and abs(idx.timestamp() - trade.get("touch_time", time.time())) < 120) if hasattr(idx,'timestamp') else False
                    if not tp1_hit:
                        if tp1_t and sl_t:
                            send_telegram(f"⚠️ SAME CANDLE TP1+SL — ORDER UNKNOWN - {name} {trend} ID {trade.get('trade_id')}\n\nCandle High {high} Low {low} Close {close}\nTP1 {tp1} SL {sl}\nCannot know which hit first without tick data.\nRecord: UNVERIFIED - No win/loss. Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "SAME CANDLE TP1+SL UNVERIFIED", "entry": entry}); save_outcomes(); trade_done=True; break
                        if tp1_t and tp2_t:
                            send_telegram(f"🎉 TP1+TP2 SAME CANDLE - +1.15R Price-Level (Order Uncertain) - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} TP1 {tp1} TP2 {tp2}\nCandle High {high} Low {low}\nPrice jumped to TP2 in one candle. Bot logs Price-Level WIN +1.15R, but order uncertain.\nReply WIN {trade.get('trade_id')} to confirm user-reported win.")
                            PRICE_LEVEL_WINS[name]=PRICE_LEVEL_WINS.get(name,0)+1
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TP1+TP2 SAME CANDLE +1.15R Price-Level", "entry": entry, "r": 1.15}); save_outcomes(); trade_done=True; break
                        if tp1_t:
                            trade["tp1_hit"]=True
                            send_telegram(f"🔔 TP1 TOUCHED - Alert Only, NOT Win - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} TP1 {tp1} (0.8R)\nCandle High {high} Low {low}\nACTION FOR YOU:\n1. Close 50% YOURSELF\n2. MOVE SL TO ENTRY YOURSELF\n3. Reply BE DONE {trade.get('trade_id')} when you moved SL\nUntil you reply BE DONE, bot still watches ORIGINAL SL {sl}, NOT BE.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TP1 TOUCHED - Waiting BE DONE", "entry": entry}); save_outcomes()
                            # Update last processed to this candle
                            LAST_PROCESSED_1M[name]=idx.timestamp() if hasattr(idx,'timestamp') else time.time()
                            continue
                        if sl_t:
                            send_telegram(f"❌ SL TOUCHED - LOSS -1R Price-Level - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} SL {sl}\nCandle High {high} Low {low}\nTrade done. Reply LOSS {trade.get('trade_id')} to record User-Reported Loss.")
                            PRICE_LEVEL_LOSSES[name]=PRICE_LEVEL_LOSSES.get(name,0)+1
                            PAIR_COOLDOWN_UNTIL[name]=time.time()+7200
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "SL -1R Price-Level", "entry": entry, "r": -1.0}); save_outcomes(); trade_done=True; break
                        # No TP/SL in this candle, update last processed
                        LAST_PROCESSED_1M[name]=idx.timestamp() if hasattr(idx,'timestamp') else time.time()
                    else:
                        # After TP1 - V36.5 FIX 4: Check BE + Original SL together before deciding
                        # If both BE return and original SL hit in same candle, order unknown
                        if be_t and orig_sl_t and not be_confirmed:
                            send_telegram(f"⚠️ SAME CANDLE BE-RETURN + ORIGINAL SL — ORDER UNKNOWN - {name} {trend} ID {trade.get('trade_id')}\n\nCandle High {high} Low {low}\nEntry (BE) {entry} Original SL {sl}\nPrice hit both entry and SL in same candle. If you moved SL, result is +0.4R. If not, -0.1R.\nBot cannot know order. Record: UNVERIFIED - Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "SAME CANDLE BE+SL UNVERIFIED", "entry": entry}); save_outcomes(); trade_done=True; break
                        if tp2_t and be_t:
                            send_telegram(f"⚠️ SAME CANDLE TP2+BE — ORDER UNKNOWN - {name} {trend} ID {trade.get('trade_id')}\n\nCandle High {high} Low {low}\nTP2 {tp2} BE {entry}\nCannot know if TP2 hit before BE.\nRecord: UNVERIFIED - No win/loss. Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "SAME CANDLE TP2+BE UNVERIFIED", "entry": entry}); save_outcomes(); trade_done=True; break
                        if tp2_t:
                            send_telegram(f"🎉 TP2 TOUCHED - WIN +1.15R Price-Level - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} TP1 {tp1} TP2 {tp2} Total 1.15R (0.4+0.75)\nCandle High {high} Low {low}\nReply WIN {trade.get('trade_id')} to make it User-Reported (counts for Hot Pair). Price-Level alone does NOT count.")
                            PRICE_LEVEL_WINS[name]=PRICE_LEVEL_WINS.get(name,0)+1
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TP2 +1.15R Price-Level", "entry": entry, "r": 1.15}); save_outcomes(); trade_done=True; break
                        if be_t and be_confirmed:
                            send_telegram(f"⚠️ BE TOUCHED AFTER TP1 - UNVERIFIED - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} BE confirmed by you earlier.\nTP1 was touched. Net IF you closed 50%: +0.4R\nBot records: TP1+BE UNVERIFIED - No win/loss counted.\nReply WIN {trade.get('trade_id')} if broker shows profit.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TP1+BE UNVERIFIED - No Win Counted", "entry": entry}); save_outcomes(); trade_done=True; break
                        if be_t and not be_confirmed:
                            send_telegram(f"⚠️ PRICE RETURNED TO ENTRY AFTER TP1 - BE NOT CONFIRMED - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} Candle Low/High {low}/{high}\nYou never replied BE DONE {trade.get('trade_id')}, so bot does NOT know if you moved SL.\nBot keeps watching ORIGINAL SL {sl}. Reply BE DONE {trade.get('trade_id')} if you moved SL.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "Price Returned to Entry - BE Not Confirmed", "entry": entry}); save_outcomes()
                            LAST_PROCESSED_1M[name]=idx.timestamp() if hasattr(idx,'timestamp') else time.time()
                            continue
                        if sl_t:
                            send_telegram(f"⚠️ ORIGINAL SL HIT AFTER TP1 - UNVERIFIED - {name} {trend} ID {trade.get('trade_id')}\n\nEntry {entry} Original SL {sl}\nYou never confirmed BE DONE, so bot does not know if you moved SL.\nIf you DID move SL, real result is +0.4R. If not, -0.1R.\nRecord: UNVERIFIED - Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                            OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TP1+Original SL UNVERIFIED", "entry": entry}); save_outcomes(); trade_done=True; break
                        LAST_PROCESSED_1M[name]=idx.timestamp() if hasattr(idx,'timestamp') else time.time()
                if not trade_done:
                    touch_age=(time.time()-trade.get("touch_time",trade["time"]))/60
                    if touch_age>20 and not trade.get("tp1_hit"):
                        send_telegram(f"⏰ TIMEOUT 20m - {name} {trade['trend']} ID {trade.get('trade_id')}\n\nTP1 not touched. Exit on broker. Reply WIN/LOSS {trade.get('trade_id')} to record user-reported result.")
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "TIMEOUT", "entry": trade["entry"]}); save_outcomes()
                    elif touch_age>60:
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade.get('trade_id'), "result": "WATCH EXPIRED", "entry": trade["entry"]}); save_outcomes()
                    else: remaining.append(trade)
                save_outcomes()
        except Exception as e:
            print(f"Check trade error {e}", flush=True); remaining.append(trade)
    ACTIVE_TRADES=remaining
    save_outcomes()

def analyze(name, ysym, now):
    # NO REDUCTION: Same analyze as V36.4, only Daily/Weekly + Order Type fixes
    df_4h = get_data(ysym, "4h", "60d")
    df_1h = get_data(ysym, "1h", "10d")
    df_15m = get_data(ysym, "15m", "10d")
    df_5m = get_data(ysym, "5m", "2d")
    df_1m = get_data(ysym, "1m", "1d")
    df_1d = get_data(ysym, "1d", "60d")
    df_1w = get_data(ysym, "1wk", "6mo")
    if df_15m is None or df_5m is None or df_1h is None or df_4h is None:
        return {"fail": True, "score": 0, "reason": "No data"}
    if len(df_5m)<50 or len(df_15m)<50 or len(df_1h)<20 or len(df_4h)<20:
        return {"fail": True, "score": 0, "reason": "Not enough candles"}
    df_4h = df_4h.iloc[:-1]; df_1h = df_1h.iloc[:-1]; df_15m = df_15m.iloc[:-1]; df_5m = df_5m.iloc[:-1]
    if df_1m is not None and len(df_1m)>5: df_1m = df_1m.iloc[:-1]
    c15=df_15m['Close'].tolist(); c5=df_5m['Close'].tolist(); c1=df_1h['Close'].tolist(); c4=df_4h['Close'].tolist()
    h5=df_5m['High'].tolist(); l5=df_5m['Low'].tolist(); h15=df_15m['High'].tolist(); l15=df_15m['Low'].tolist()
    v15 = df_15m['Volume'].tolist() if 'Volume' in df_15m.columns else None
    score=0; details=[]; warnings=[]; smart_notes=[]; tags=[]
    trend_strength = 50; trend_label = "NORMAL"
    e50_4h=ema(c4,50); e20_4h=ema(c4,20); p4=c4[-1]
    htf4="BUY" if p4>e50_4h and p4>e20_4h else ("SELL" if p4<e50_4h and p4<e20_4h else None)
    e50_1h=ema(c1,50); e20_1h=ema(c1,20); p1=c1[-1]
    htf="BUY" if p1>e50_1h and p1>e20_1h else ("SELL" if p1<e50_1h and p1<e20_1h else None)
    e50=ema(c15,50); e200=ema(c15,200); price=c15[-1]; trend=None
    if price>e50 and price>e200: trend="BUY"; score+=20; details.append("EMA 50+200 BUY +20")
    elif price<e50 and price<e200: trend="SELL"; score+=20; details.append("EMA 50+200 SELL +20")
    else: return {"fail":True,"score":score,"reason":f"EMA fail {price:.5f} vs {e50:.5f}/{e200:.5f}","trend":"NONE"}
    adx_val, plus_di, minus_di = adx_calc_true_wilder(h15, l15, c15, 14)
    adx_label = "WEAK"
    if adx_val >= 25: adx_label = "STRONG"; score+=5; details.append(f"ADX True Wilder {adx_val} STRONG +5 BONUS"); tags.append(f"ADX STRONG {adx_val}"); smart_notes.append(f"ADX {adx_val} Strong")
    elif adx_val >= 20: adx_label = "NORMAL"; details.append(f"ADX True Wilder {adx_val} Normal"); tags.append(f"ADX {adx_val}")
    else: adx_label = "WEAK"; details.append(f"ADX True Wilder {adx_val} Weak - Tag only"); tags.append(f"ADX WEAK {adx_val}")
    try:
        dist_pct = abs(price - e50) / price * 100 if price!=0 else 0
        ema_series = []; tmp = c15[0]; k = 2/(50+1)
        for v in c15: tmp = v*k + tmp*(1-k); ema_series.append(tmp)
        slope = (ema_series[-1] - ema_series[-5]) / ema_series[-5] * 100 if len(ema_series)>=6 and ema_series[-5]!=0 else 0
        slope_val = slope if trend=="BUY" else -slope
        trend_strength = min(100, max(0, 50 + dist_pct*800 + slope_val*800))
        if trend_strength >= 75: trend_label = "STRONG"; score+=5; details.append(f"TREND STRONG {trend_strength:.0f}% +5 BONUS"); tags.append(f"TREND STRONG {trend_strength:.0f}%"); smart_notes.append("Strong Trend")
        elif trend_strength >= 55: trend_label = "NORMAL"; details.append(f"Trend Normal {trend_strength:.0f}%"); tags.append(f"TREND NORMAL {trend_strength:.0f}%")
        else: trend_label = "WEAK"; details.append(f"Trend Weak {trend_strength:.0f}% - Tag only"); tags.append(f"TREND WEAK {trend_strength:.0f}%")
    except Exception as e: details.append(f"Trend Error {e}")
    if htf is not None and htf==trend: score+=5; details.append(f"1H Agrees {htf} +5"); tags.append("1H AGREE")
    elif htf is not None and htf!=trend: details.append(f"1H Conflict {htf} - Tag only"); tags.append("1H CONFLICT"); warnings.append(f"1H vs 15m {htf}")
    if htf4 is not None and htf4==trend: score+=5; details.append(f"4H Agrees {htf4} +5"); tags.append("4H AGREE"); smart_notes.append("4H Agree")
    elif htf4 is not None and htf4!=trend: details.append(f"4H Conflict {htf4} - Tag only"); tags.append("4H CONFLICT"); warnings.append(f"4H vs 15m {htf4}")
    daily_label = "None"; weekly_label = "None"
    try:
        # V36.5 FIX: Daily/Weekly - Use last completed candle correctly
        # If last daily candle is today and market still open, it's unfinished -> use -2. If market closed/weekend, last is completed -> use -1
        if df_1d is not None and len(df_1d) >= 2:
            # Check if last daily timestamp is today
            try:
                last_daily_date = df_1d.index[-1].date()
                today_date = now.date()
                use_idx = -2 if last_daily_date == today_date else -1
            except:
                use_idx = -2
            d_high = float(df_1d['High'].iloc[use_idx]); d_low = float(df_1d['Low'].iloc[use_idx])
            if trend=="BUY" and price > d_high: score+=5; daily_label="ABOVE DAILY HIGH"; details.append("DAILY Above High +5 BONUS"); tags.append("ABOVE DAILY HIGH"); smart_notes.append("Above Daily High")
            elif trend=="SELL" and price < d_low: score+=5; daily_label="BELOW DAILY LOW"; details.append("DAILY Below Low +5 BONUS"); tags.append("BELOW DAILY LOW"); smart_notes.append("Below Daily Low")
            else: daily_label="INSIDE DAILY RANGE"; details.append("Daily: Inside Range"); tags.append("INSIDE DAILY RANGE")
        if df_1w is not None and len(df_1w) >= 2:
            try:
                # Weekly: if last weekly is current week (unfinished), use -2 else -1
                # Approx: if today is Monday, last weekly is previous completed, use -1. Else use -2 for current week unfinished
                use_w = -1 if now.weekday()==0 else -2
                if len(df_1w) < 3: use_w = -1
            except:
                use_w = -2
            w_high = float(df_1w['High'].iloc[use_w]); w_low = float(df_1w['Low'].iloc[use_w])
            if trend=="BUY" and price > w_high: weekly_label="ABOVE WEEKLY HIGH"; tags.append("ABOVE WEEKLY HIGH"); details.append("Weekly: Above High")
            elif trend=="SELL" and price < w_low: weekly_label="BELOW WEEKLY LOW"; tags.append("BELOW WEEKLY LOW"); details.append("Weekly: Below Low")
            else: weekly_label="INSIDE WEEKLY"; details.append("Weekly: Inside Range")
    except Exception as e: details.append(f"Daily/Weekly Error {e}")
    pb = PAIR_BONUS.get(name, 0)
    if pb!=0: score+=pb; details.append(f"Pair Bonus {name} +{pb} (Arbitrary - Review Note)")
    wins = USER_REPORTED_WINS.get(name, 0); losses = USER_REPORTED_LOSSES.get(name, 0)
    pwins = PRICE_LEVEL_WINS.get(name,0); plosses = PRICE_LEVEL_LOSSES.get(name,0)
    hot_score = wins - losses
    if wins >= 2 and wins > losses:
        score+=5; details.append(f"HOT PAIR {name} - {wins}W {losses}L USER-REPORTED +5 BONUS (Price-Level {pwins}W {plosses}L ignored)"); tags.append(f"HOT PAIR 🔥 {wins}W {losses}L USER-REPORTED"); smart_notes.append("Hot Pair - User-Reported Wins")
    elif losses >= 2 and losses > wins:
        details.append(f"COLD PAIR {name} - {wins}W {losses}L USER-REPORTED - Tag only"); tags.append(f"COLD PAIR ❄️ {wins}W {losses}L USER-REPORTED")
    else:
        details.append(f"Pair record User-Reported: {wins}W {losses}L | Price-Level: {pwins}W {plosses}L (Price-Level does NOT give bonus)")
    sess_label, is_prime = get_session_label(now)
    if is_prime: score+=5; details.append(f"{sess_label} +5 - PRIME"); tags.append("PRIME SESSION"); smart_notes.append(sess_label)
    else: details.append(f"{sess_label} - Tag only"); tags.append(sess_label); smart_notes.append(sess_label)
    r=rsi_calc_wilder(c15,14)
    if trend=="BUY" and r>80: return {"fail":True,"score":score,"reason":f"RSI Wilder {r:.1f} extreme","trend":trend,"details":details}
    if trend=="SELL" and r<20: return {"fail":True,"score":score,"reason":f"RSI Wilder {r:.1f} extreme","trend":trend,"details":details}
    details.append(f"RSI Wilder {r:.1f}")
    if r>70 and trend=="BUY": tags.append("REVERSAL RISK - RSI High")
    if r<30 and trend=="SELL": tags.append("REVERSAL RISK - RSI Low")
    atr_val=atr_calc_wilder(h5,l5,c5,14)
    if atr_val<=0: atr_val=abs(price*0.001)
    atr_ratio = atr_val / price if price!=0 else 0.001
    if atr_ratio > 0.0015: threshold = 78; tags.append("HIGH MOMENTUM"); details.append(f"Adaptive Threshold 78 - Volatile")
    elif atr_ratio < 0.0008: threshold = 82; tags.append("LOW VOLUME"); details.append(f"Adaptive Threshold 82 - Quiet")
    else: threshold = 80; tags.append("NORMAL MOMENTUM"); details.append(f"Adaptive Threshold 80 - Normal")
    vol_warning = "Check broker spread"
    try:
        last_range = h5[-1] - l5[-1]
        if atr_val > 0 and last_range / atr_val > 1.2: vol_warning = "HIGH VOLATILITY - Check broker spread"; tags.append("HIGH VOLATILITY - Check broker spread"); details.append("Volatility: High - Check broker spread")
        else: vol_warning = "Volatility Normal - Still check broker spread"; details.append("Volatility: Normal - Still check broker spread")
    except: pass
    last_8_range = max(h15[-8:]) - min(l15[-8:])
    is_chop = last_8_range / price < 0.004
    if is_chop: tags.append("CHOP - Tight Range"); details.append(f"CHOP Tag - Range {last_8_range:.5f}"); smart_notes.append("Chop")
    else: details.append(f"Chop OK - Range {last_8_range:.5f}")
    if name=="XAUUSD" and atr_val>6.0: return {"fail":True,"score":score,"reason":f"Gold ATR too high {atr_val:.2f}","trend":trend,"details":details}
    last3=c5[-1]-c5[-4]; chase=False
    if trend=="BUY" and last3>atr_val*0.6: chase=True; warnings.append("CHASE"); details.append("CHASE - Use LIMIT"); tags.append("CHASE - Use Limit")
    elif trend=="SELL" and last3<-atr_val*0.6: chase=True; warnings.append("CHASE - Use LIMIT"); details.append("CHASE - Use Limit")
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
    vol_label = "No Volume Data"
    try:
        if v15 is not None and len(v15) >= 20:
            avg_vol = sum(v15[-20:-1]) / 19; last_vol = v15[-1]
            if avg_vol > 0 and last_vol > avg_vol * 1.5: vol_label = "HIGH VOLUME"; score+=5; details.append(f"VOLUME HIGH Completed {last_vol:.0f} vs avg {avg_vol:.0f} +5 BONUS"); tags.append("HIGH VOLUME"); smart_notes.append("High Volume")
            elif avg_vol > 0 and last_vol < avg_vol * 0.5: vol_label = "LOW VOLUME"; details.append(f"Volume Low Completed {last_vol:.0f} vs avg {avg_vol:.0f}"); tags.append("LOW VOLUME")
            else: vol_label = "NORMAL VOLUME"; details.append(f"Volume Normal Completed {last_vol:.0f}")
    except Exception as e: details.append(f"Volume Error {e}")
    structure_tag = "None"
    try:
        last_highs = h15[-20:]; last_lows = l15[-20:]
        max_h = max(last_highs); min_l = min(last_lows)
        touches_high = sum(1 for h in last_highs if abs(h - max_h)/max_h < 0.001)
        touches_low = sum(1 for l in last_lows if abs(l - min_l)/min_l < 0.001)
        if trend=="SELL" and touches_high >= 2: structure_tag = "DOUBLE TOP"; tags.append("DOUBLE TOP"); details.append("Structure: Double Top"); smart_notes.append("Double Top")
        elif trend=="BUY" and touches_low >= 2: structure_tag = "DOUBLE BOTTOM"; tags.append("DOUBLE BOTTOM"); details.append("Structure: Double Bottom"); smart_notes.append("Double Bottom")
        elif bos_ok: structure_tag = "CLEAN BREAKOUT"; score+=5; details.append("CLEAN BREAKOUT +5 BONUS"); tags.append("CLEAN BREAKOUT"); smart_notes.append("Clean Breakout")
        else: details.append("Structure: Normal")
    except Exception as e: details.append(f"Structure Error {e}")
    prev_low=min(l5[-20:-2]); prev_high=max(h5[-20:-2])
    sweep_ok=False; sweep_bonus=False; sweep_level="Local"
    y_high=None; y_low=None
    if df_1d is not None and len(df_1d)>=2:
        try: y_high=float(df_1d['High'].iloc[-2]); y_low=float(df_1d['Low'].iloc[-2])
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
        score+=15; details.append(f"Sweep +15 ({sweep_level})"); tags.append(f"LIQUIDITY SWEEP {sweep_level}")
        if sweep_bonus: score+=15; details.append(f"SMART Sweep +15 BONUS"); smart_notes.append(f"Sweep {sweep_level}")
    else: details.append("Sweep 0")
    bos5=False
    for i in range(len(df_5m)-5, len(df_5m)-1):
        if trend=="BUY" and c5[i]>max(h5[i-5:i]): bos5=True
        if trend=="SELL" and c5[i]<min(l5[i-5:i]): bos5=True
    if bos5: score+=10; details.append("5m BOS +10")
    fvg_found=False; fvg_top=0; fvg_bottom=0; fvg_ok=False; fvg_inside=False; fvg_fresh=False; fvg_distance_ok=True
    for i in range(len(df_5m)-3, max(len(df_5m)-11, 2), -1):
        if i<2 or i+2>=len(df_5m): continue
        h1=h5[i]; l1=l5[i]; h3=h5[i+2]; l3=l5[i+2]
        if trend=="BUY" and h1<l3:
            mitigated=False
            for k in range(i+3, len(df_5m)):
                if l5[k] <= l3 and h5[k] >= h1: mitigated=True; break
            if not mitigated:
                fvg_bottom=h1; fvg_top=l3; fvg_found=True; fvg_fresh=True; break
        if trend=="SELL" and l1>h3:
            mitigated=False
            for k in range(i+3, len(df_5m)):
                if h5[k] >= h3 and l5[k] <= l1: mitigated=True; break
            if not mitigated:
                fvg_top=l1; fvg_bottom=h3; fvg_found=True; fvg_fresh=True; break
    current=c5[-1]
    if fvg_found:
        fvg_mid = (fvg_top+fvg_bottom)/2
        dist_pct = abs(current - fvg_mid)/current if current!=0 else 0
        if dist_pct > 0.003:
            fvg_distance_ok=False
            details.append(f"FVG FAR {dist_pct*100:.2f}% away - Does NOT count"); tags.append("FVG FAR - Does NOT count"); fvg_ok=False
        elif fvg_bottom<=current<=fvg_top: fvg_inside=True; score+=20; details.append("FVG INSIDE FRESH Overlap-Checked +20"); fvg_ok=True
        else: score+=10; details.append("FVG Near Fresh Overlap-Checked +10"); fvg_ok=True
    else: details.append("FVG 0 - No fresh unmitigated FVG (overlap-checked)")
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
    if repeat_count >= 2: tags.append(f"REPEAT - {repeat_count}th signal today")
    freshness = 100; freshness_label = "FRESH"
    try:
        last_time = PAIR_LAST_SIGNAL_TIME.get(name)
        if last_time:
            mins_ago = (time.time() - last_time) / 60
            if mins_ago < 5: freshness = 100; freshness_label = "FRESH"
            elif mins_ago < 15: freshness = 80; freshness_label = "FRESH"
            elif mins_ago < 30: freshness = 60; freshness_label = "STALE"
            else: freshness = 100; freshness_label = "FRESH - New cycle"
            tags.append(f"{freshness_label} - Last {mins_ago:.0f}m ago"); details.append(f"Freshness: {freshness_label} ({mins_ago:.0f}m ago)")
        else: tags.append("FRESH - First today"); details.append("Freshness: FRESH - First today")
    except: pass
    raw_score=score; score=min(score,120)
    score_pct=round((score/120)*100,1)
    print(f"{name} {trend} Score {score}/120 Raw {raw_score} Thr {threshold} RSI Wilder {r:.1f} ADX True {adx_val} OB {ob_type} FVG {fvg_fresh} DistOK {fvg_distance_ok} 1m {m1_ok} Trend {trend_label} {trend_strength:.0f}% Struct {structure_tag} Fresh {freshness_label} User {wins}W {losses}L Price {pwins}W {plosses}L", flush=True)
    if score < threshold - 10: return {"fail":True,"score":score,"reason":f"Score {score} < Watch {threshold-10}","trend":trend,"details":details,"threshold":threshold}
    if score < threshold: return {"watch":True,"score":score,"trend":trend,"details":details,"symbol":name,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"smart_notes":smart_notes,"ob_type":ob_type,"tags":tags,"threshold":threshold,"sess_label":sess_label,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label,"adx":adx_val,"adx_label":adx_label,"vol_label":vol_label,"daily_label":daily_label,"weekly_label":weekly_label,"score_pct":score_pct}
    conf=sum([1 for x in [bos_ok,sweep_ok,fvg_ok,ob_ok] if x])
    if score>=90 and conf>=3 and not chase and (fvg_inside or ob_inside) and rej and m1_ok: tier="S-TIER SCALP"; tier_label="S"; take_it="YES - BEST SIGNAL"
    elif score>=80 and conf>=3 and not chase: tier="A-TIER SCALP"; tier_label="A"; take_it="YES - SCALP 0.01 only"
    elif score>=threshold and conf>=2: tier="B-TIER SCALP"; tier_label="B"; take_it="CAUTION - Smaller / Skip if unsure"
    else: return {"fail":True,"score":score,"reason":f"Quality Gate FAIL - Need 2/4, got {conf}/4","trend":trend,"details":details,"threshold":threshold}
    if ob_ok and fvg_found and fvg_distance_ok:
        if ob_inside or abs(ob_mid-current)<abs(((fvg_top+fvg_bottom)/2)-current): entry=ob_mid; entry_type=f"OB {ob_type}"
        else: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG FRESH Overlap-Checked"
    elif ob_ok: entry=ob_mid; entry_type=f"OB {ob_type}"
    elif fvg_found and fvg_distance_ok: entry=(fvg_top+fvg_bottom)/2; entry_type="FVG FRESH Overlap-Checked"
    else: entry=current; entry_type="Live Price - FVG Far/Stale"
    # V36.5 FIX: Validate LIMIT placement
    # BUY LIMIT must be BELOW live price, SELL LIMIT must be ABOVE live price. Else MARKET or invalid
    if abs(entry-current)/current < 0.0005:
        order_type="BUY MARKET" if trend=="BUY" else "SELL MARKET"
    else:
        if trend=="BUY" and entry < current:
            order_type="BUY LIMIT"
        elif trend=="SELL" and entry > current:
            order_type="SELL LIMIT"
        elif trend=="BUY" and entry > current:
            # BUY LIMIT above market is invalid - would be STOP. Use MARKET with warning
            order_type="BUY MARKET (Entry was above live - corrected from LIMIT)"
            entry=current
            entry_type="Live Price - Corrected (BUY LIMIT above market invalid)"
        elif trend=="SELL" and entry < current:
            order_type="SELL MARKET (Entry was below live - corrected from LIMIT)"
            entry=current
            entry_type="Live Price - Corrected (SELL LIMIT below market invalid)"
        else:
            order_type="BUY MARKET" if trend=="BUY" else "SELL MARKET"
    sl_mult = 1.0 if atr_ratio>0.0015 else (0.7 if atr_ratio<0.0008 else 0.8)
    fvg_for_sl = fvg_found and fvg_distance_ok
    if trend=="BUY":
        inval=ob_bottom if ob_ok else (fvg_bottom if fvg_for_sl else entry-atr_val)
        sl=max(inval-atr_val*0.2, entry-atr_val*sl_mult)
        risk=entry-sl
        if risk<atr_val*0.4: sl=entry-atr_val*0.7; risk=entry-sl
    else:
        inval=ob_top if ob_ok else (fvg_top if fvg_for_sl else entry+atr_val)
        sl=min(inval+atr_val*0.2, entry+atr_val*sl_mult)
        risk=sl-entry
        if risk<atr_val*0.4: sl=entry+atr_val*0.7; risk=sl-entry
    if risk<=0: return {"fail":True,"score":score,"reason":"Risk <=0","trend":trend}
    max_r=MAX_RISK.get(name,9999)
    if risk>max_r: return {"fail":True,"score":score,"reason":f"SL Cap {risk:.5f} > {max_r}","trend":trend,"details":details}
    if risk/entry>0.01: return {"fail":True,"score":score,"reason":"SL too wide","trend":trend,"details":details}
    if trend=="BUY": tp1=entry+risk*0.8; tp2=entry+risk*1.5
    else: tp1=entry-risk*0.8; tp2=entry-risk*1.5
    risk_dist = round(float(risk),5)
    return {"symbol":name,"trend":trend,"order_type":order_type,"entry":round(float(entry),5),"sl":round(float(sl),5),"tp1":round(float(tp1),5),"tp2":round(float(tp2),5),"score":score,"score_pct":score_pct,"details":details,"tier":tier,"tier_label":tier_label,"take_it":take_it,"live_price":round(float(current),5),"rsi":round(float(r),1),"htf":htf,"htf4":htf4,"entry_type":entry_type,"ob_type":ob_type,"ob_quality":ob_quality,"ob_top":round(float(ob_top),5) if ob_ok else 0,"ob_bottom":round(float(ob_bottom),5) if ob_ok else 0,"warnings":warnings,"chase":chase,"fvg_inside":fvg_inside,"fvg_fresh":fvg_fresh,"fvg_distance_ok":fvg_distance_ok,"rejection":rej,"inval":round(float(sl),5),"smart_notes":smart_notes,"sl_mult":sl_mult,"sweep_level":sweep_level if sweep_ok else "None","m1_ok":m1_ok,"tags":tags,"threshold":threshold,"sess_label":sess_label,"is_chop":is_chop,"is_prime":is_prime,"trend_strength":round(float(trend_strength),0),"trend_label":trend_label,"structure_tag":structure_tag,"freshness":freshness_label,"freshness_pct":freshness,"hot_score":hot_score,"wins":wins,"losses":losses,"pwins":pwins,"plosses":plosses,"adx":adx_val,"adx_label":adx_label,"vol_label":vol_label,"daily_label":daily_label,"weekly_label":weekly_label,"vol_warning":vol_warning,"risk_dist":risk_dist}

def bot_loop():
    load_outcomes()
    send_telegram("V36.5 RELIABILITY LIVE - NO REDUCTION\n12 Pairs | Adaptive 78-82 | S 90+ / A 80+ / B / WATCH\nFIXED V36.5: Trade ID Required | Telegram Chat Locked | No Old Candle Fill | Entry Candle Processed | Expiry From Confirm | BE+SL Same Candle UNVERIFIED | USER-REPORTED (Not Broker API) | Duplicate WIN Blocked | LIMIT Validated | LAST_PROCESSED Saved | Daily Reset\nHOW TO USE - WITH TRADE ID:\nSignal has ID like T001\n1. Reply CONFIRM T001 if you place it\n2. At TP1 -> Close 50%, Move SL, Reply BE DONE T001\n3. At end -> Reply WIN T001 or LOSS T001 (User-Reported, NOT Broker API)\n4. Skip -> Reply SKIP T001\nNo CONFIRM = No Tracking. No BE DONE = BE Not Assumed.\nHot Pair ONLY from YOUR User-Reported Wins, NOT Yahoo.\nSCALPER: TP 0.8R + 1.5R = 1.15R Total | Max Hold 20m\nSessions: 09-21 EAT | GOLD 11-19\nWARNING: Yahoo = Futures Proxy - Check Broker Price | /tmp may reset - Screenshot Sunday")
    sent=set(); last_hunt=time.time(); last_sent_time={}; last_debug=time.time(); debug_reasons={}
    watch_sent=set()
    while True:
        try:
            tz=pytz.timezone("Africa/Nairobi"); now=dt.now(tz); hour=now.hour+now.minute/60.0
            maybe_reset_daily(now)
            poll_telegram_commands()
            check_active_trades(now)
            if now.weekday()>=5:
                print(f"WEEKEND {now} - TEST MODE", flush=True)
                try:
                    test_pairs = ["EURUSD", "GBPUSD", "XAUUSD"]
                    pick = test_pairs[now.minute % len(test_pairs)]
                    ysym = SYMBOLS[pick]
                    res = analyze(pick, ysym, now)
                    print(f"TEST {pick}: {res}", flush=True)
                    if now.minute % 30 == 0 and now.second < 65:
                        if res and not res.get("fail"):
                            # V36.5 FIX: Weekend test - NO CONFIRM instruction, analysis only
                            send_telegram(f"TEST MODE - Weekend Analysis Only\n\nPair: {pick}\nScore: {res.get('score')}/120 ({res.get('score_pct')}%)\nTier: {res.get('tier')}\nTrend: {res.get('trend')} | ADX True {res.get('adx')} | RSI Wilder {res.get('rsi')}\nFVG Overlap: {res.get('fvg_fresh')} DistOK {res.get('fvg_distance_ok')} | Order: {res.get('order_type')}\nUser-Reported: {res.get('wins')}W {res.get('losses')}L | Price-Level {res.get('pwins')}W {res.get('plosses')}L\nTEST only - No trade created. Do NOT reply CONFIRM. Real signals Monday 09:00 EAT.")
                        elif res and res.get("watch"):
                            send_telegram(f"TEST MODE - Weekend Analysis Only\n\nPair: {pick}\nScore: {res.get('score')}/120 - WATCH\nTEST only - No trade created. Real signals Monday 09:00 EAT.")
                        else:
                            send_telegram(f"TEST MODE - Weekend Analysis Only\n\nPair: {pick}\nScore: {res.get('score',0)}/120 - {res.get('reason','No setup')}\nBot analyzing correctly. Real signals Monday 09:00 EAT.")
                except Exception as e: print(f"TEST FAILED: {e}", flush=True); traceback.print_exc()
                time.sleep(60); continue
            if not (9.0<=hour<21.0):
                print(f"OUT OF SESSION {now} - alive", flush=True); time.sleep(60); continue
            if is_news_time(now):
                print(f"NEWS TIME {now} - blocked", flush=True); time.sleep(300); continue
            sess_label, is_prime = get_session_label(now)
            print(f"V36.5 checking {now} Session {sess_label} Active {len(ACTIVE_TRADES)} User {USER_REPORTED_WINS}/{USER_REPORTED_LOSSES} Price {PRICE_LEVEL_WINS}/{PRICE_LEVEL_LOSSES}", flush=True)
            found=0; best_score=0; best_pair="None"
            for name, ysym in SYMBOLS.items():
                try:
                    if name in PAIR_COOLDOWN_UNTIL and time.time() < PAIR_COOLDOWN_UNTIL[name]: continue
                    if name=="XAUUSD" and not (11.0<=hour<19.0): continue
                    if any(t["pair"]==name for t in ACTIVE_TRADES):
                        print(f"{name} already tracked - skipping", flush=True); continue
                    print(f"Checking {name}...", flush=True)
                    res=analyze(name, ysym, now)
                    if res and res.get("watch"):
                        wkey=f"{name}-{res['trend']}"
                        if wkey not in watch_sent:
                            watch_sent.add(wkey)
                            tags_txt=" | ".join(res.get('tags',[])) or "Getting ready"
                            send_telegram(f"WATCH - Score {res['score']}/120 ({res.get('score_pct')}%) (Need {res['threshold']})\n\nPair: {name}\nDirection: {res['trend']}\nLive: {res['live_price']}\nRSI Wilder: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSession: {res['sess_label']}\nTrend: {res.get('trend_label')} {res.get('trend_strength')}%\nADX True: {res.get('adx')} {res.get('adx_label')} | Vol: {res.get('vol_label')}\nFVG Overlap: {res.get('fvg_fresh')} DistOK {res.get('fvg_distance_ok')}\nStructure: {res.get('structure_tag')} | Daily: {res.get('daily_label')} | Fresh: {res.get('freshness')}\nTags: {tags_txt}\n\n" + "\n".join(res['details']) + "\n\nNot a trade yet. Wait for S/A Tier.")
                        continue
                    if res and not res.get("fail"):
                        found+=1; last_hunt=time.time()
                        cool=f"{name}-{res['trend']}-{res['tier_label']}"
                        if cool in last_sent_time and (time.time()-last_sent_time[cool])<1200: continue
                        key=f"{name}-{res['trend']}-{res['entry']}-{res['tier_label']}"
                        if key in sent: continue
                        warn_txt="\n".join([f"WARNING: {w}" for w in res.get('warnings',[])]) if res.get('warnings') else "No warnings"
                        smart_txt=", ".join(res.get('smart_notes',[])) or "None"
                        tags_txt=" | ".join(res.get('tags',[])) or "None"
                        lot_advice=f"Lot: Use Formula Below - {res['tier_label']}-TIER"
                        if res.get('is_chop'): lot_advice += "\nCHOP - Half risk or skip"
                        if res.get('trend_label')=="WEAK": lot_advice += "\nWEAK TREND - Move to BE fast"
                        lot_advice += f"\n\nHONEST LOT CALC:\nSL Distance = {res.get('risk_dist')}\nFormula: Lot = (Balance x 1%) / (SL Distance x $ per 1.0 lot)\nCheck YOUR broker contract size. Safe default: 0.01"
                        trade_id = new_trade_id()
                        msg=f"{res['tier']} - Score {res['score']}/120 ({res.get('score_pct')}% Score, NOT Win Chance) - {res['sess_label']} - ID {trade_id}\nThreshold: {res['threshold']}\n\nPair: {res['symbol']}\nDirection: {res['trend']}\nOrder: {res['order_type']} - PENDING CONFIRM\nEntry Type: {res['entry_type']} - {res['ob_quality']}\nLive: {res['live_price']} (Yahoo Proxy - CHECK BROKER PRICE!)\nEntry: {res['entry']}\nOB: {res['ob_bottom']} - {res['ob_top']} ({res['ob_type']})\nSL: {res['sl']} (x{res['sl_mult']})\nTP1: {res['tp1']} (0.8R)\nTP2: {res['tp2']} (1.5R)\nTOTAL IF BOTH: 1.15R (0.4+0.75) - NOT 1.5R\nRSI Wilder: {res['rsi']} | 1H: {res['htf']} | 4H: {res['htf4']}\nSweep: {res['sweep_level']} | 1m Confirm: {res['m1_ok']}\nSmart: {smart_txt}\nTags: {tags_txt}\nV36.5: Trend {res.get('trend_label')} {res.get('trend_strength')}% | ADX True {res.get('adx')} {res.get('adx_label')} | Vol: {res.get('vol_label')} | Daily: {res.get('daily_label')} | Weekly: {res.get('weekly_label')} | FVG Overlap: {res.get('fvg_fresh')} DistOK {res.get('fvg_distance_ok')} | Fresh: {res.get('freshness')}\nUser-Reported: {res.get('wins')}W {res.get('losses')}L | Price-Level: {res.get('pwins')}W {res.get('plosses')}L (Price-Level does NOT give Hot bonus)\n\n" + "\n".join(res['details']) + f"\n\nWARNINGS:\n{warn_txt}\n\nDECISION: {res['take_it']}\n\n⚠️ CONFIRM REQUIRED - USE TRADE ID:\nReply CONFIRM {trade_id} IF you place this order on broker.\nIf you don't reply in 10m, bot will NOT track it.\nReply SKIP {trade_id} if you skip it.\nAt TP1: Close 50%, Move SL to BE, Reply BE DONE {trade_id}\nAt end: Reply WIN {trade_id} or LOSS {trade_id} to record USER-REPORTED result (Not Broker API).\n\nSCALPER RULES:\n{lot_advice}\nTP1: Close 50%, MOVE SL TO ENTRY YOURSELF\nTP2: Close 50% - Total 1.15R\nMAX HOLD 20m AFTER TOUCH\nLIMIT not touched 10m AFTER CONFIRM = CANCEL\n\nPrice-Level Estimate Only - NOT Broker-Verified. User-Reported Only."
                        delivered = send_telegram(msg)
                        if not delivered: print(f"{name} Telegram failed, NOT tracking", flush=True); continue
                        PAIR_SIGNAL_COUNT[name] = PAIR_SIGNAL_COUNT.get(name,0)+1
                        PAIR_LAST_SIGNAL_TIME[name] = time.time()
                        sent.add(key); last_sent_time[cool]=time.time()
                        LAST_SIGNAL_PRICE[key] = {"entry": res['entry'], "sl": res['sl'], "time": time.time(), "symbol": name, "trend": res['trend']}
                        OUTCOME_LOG.append({"time": now.strftime("%Y-%m-%d %H:%M"), "pair": name, "trade_id": trade_id, "tier": res['tier_label'], "trend": res['trend'], "score": res['score'], "entry": res['entry'], "sl": res['sl'], "tp1": res['tp1'], "tp2": res['tp2'], "adx": res.get('adx'), "vol": res.get('vol_label'), "status": "PENDING_CONFIRM"})
                        ACTIVE_TRADES.append({"pair": name, "trade_id": trade_id, "trend": res['trend'], "entry": res['entry'], "sl": res['sl'], "tp1": res['tp1'], "tp2": res['tp2'], "time": time.time(), "tier": res['tier_label'], "tp1_hit": False, "status": "PENDING_CONFIRM", "effective_sl": res['sl'], "be_confirmed": False})
                        save_outcomes()
                    else:
                        sc=res.get("score",0) if res else 0
                        if sc>best_score: best_score=sc; best_pair=name
                        debug_reasons[name]=f"{res.get('trend','?')} {sc}/120 - {res.get('reason','Unknown')}"
                        print(f"{name}: {sc}/120 - {res.get('reason')}", flush=True)
                except Exception as e:
                    print(f"Error {name}: {e}", flush=True); traceback.print_exc()
            if (time.time()-last_debug)>=1800:
                dbg="\n".join([f"{k}: {v}" for k,v in debug_reasons.items()])
                wins_txt = ", ".join([f"{k}:{v}W" for k,v in USER_REPORTED_WINS.items()]) or "No user-reported wins yet"
                losses_txt = ", ".join([f"{k}:{v}L" for k,v in USER_REPORTED_LOSSES.items()]) or "No user-reported losses yet"
                pw_txt = ", ".join([f"{k}:{v}W" for k,v in PRICE_LEVEL_WINS.items()]) or "No price-level wins"
                pl_txt = ", ".join([f"{k}:{v}L" for k,v in PRICE_LEVEL_LOSSES.items()]) or "No price-level losses"
                send_telegram(f"V36.5 Debug - Alive - {sess_label}\n\n{dbg}\nBest: {best_pair} {best_score}/120\nActive: {len(ACTIVE_TRADES)}\nUSER-REPORTED (Counts for Hot): Wins {wins_txt} Losses {losses_txt}\nPRICE-LEVEL (Does NOT Count): Wins {pw_txt} Losses {pl_txt}\nEAT {now.strftime('%H:%M')}\nReply CONFIRM / BE DONE / WIN / LOSS / SKIP + Trade ID (e.g. CONFIRM T001) to update bot.")
                last_debug=time.time(); debug_reasons={}; watch_sent.clear()
            if found==0 and (time.time()-last_hunt)>=1800:
                send_telegram(f"Still Hunting SCALPS - V36.5 RELIABILITY...\n\nNo S/A yet. Session: {sess_label}\nBest: {best_pair} - {best_score}/120\nActive: {len(ACTIVE_TRADES)} | User-Reported {USER_REPORTED_WINS}/{USER_REPORTED_LOSSES} | Price-Level {PRICE_LEVEL_WINS}/{PRICE_LEVEL_LOSSES}\nEAT {now.strftime('%H:%M')}")
                last_hunt=time.time()
            if len(sent)>200: sent.clear()
            gc.collect(); print("Sleeping 60s...", flush=True); time.sleep(60)
        except Exception as e:
            print(f"Loop crash, retry 10s: {e}", flush=True); traceback.print_exc(); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    print("V36.5 RELIABILITY - Flask MAIN, Bot background", flush=True)
    run_web()
