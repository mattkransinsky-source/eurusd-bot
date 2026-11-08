import os, time, threading, requests, json
import pandas as pd
import numpy as np
from flask import Flask
from datetime import datetime, timezone

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
DERIV_TOKEN = os.getenv("DERIV_API_TOKEN", "")

SYMBOLS = {"EURUSD": "EURUSD=X", "GBPJPY": "GBPJPY=X", "XAUUSD": "GC=F"}
PAIR_PIP = {"EURUSD": 0.0001, "GBPJPY": 0.01, "XAUUSD": 0.1}
MIN_SL_PIPS = {"EURUSD": 18, "GBPJPY": 30, "XAUUSD": 18}
DERIV_SYMBOLS = {"EURUSD": "frxEURUSD", "GBPJPY": "frxGBPJPY", "XAUUSD": "frxXAUUSD"}

app = Flask(__name__)
@app.route("/")
def home():
    return "V7.0 AUTO - DERIV DEMO AUTO TRADER - V6.4 BRAIN"

def send(msg):
    msg = msg.replace("%", " pct").replace("/", " per ")
    try:
        if not TOKEN or not CHAT_ID: return
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=15)
        print(f"send status: {r.status_code}")
    except Exception as e:
        print(f"Send failed: {e}")

def get_data(ysym):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        for base in ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]:
            url = f"{base}/v8/finance/chart/{ysym}?interval=15m&range=5d"
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                j = r.json()
                res = j["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                df = pd.DataFrame({"Close": q["close"], "High": q["high"], "Low": q["low"], "Open": q["open"]})
                df = df.dropna().reset_index(drop=True)
                if len(df) >= 100:
                    return df
        return None
    except Exception as e:
        print(f"Data error {ysym}: {e}")
        return None

def get_dxy():
    df = get_data("DX-Y.NYB")
    if df is None or len(df) < 5: return "NEUTRAL"
    try:
        if df["Close"].iloc[-1] > df["Close"].iloc[-5]: return "BULLISH"
        if df["Close"].iloc[-1] < df["Close"].iloc[-5]: return "BEARISH"
    except: pass
    return "NEUTRAL"

def place_deriv_trade(pair, direction, sl_pips, tp_pips):
    # AUTO TRADE ON DERIV DEMO VIA API
    if not DERIV_TOKEN:
        print("No DERIV_API_TOKEN - skipping auto, will send Telegram only")
        return "NO TOKEN - MANUAL ONLY"
    try:
        import websocket
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=20)
        # Authorize
        ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        auth = json.loads(ws.recv())
        if "error" in auth:
            print(f"Deriv auth error: {auth['error']}")
            ws.close()
            return f"AUTH FAILED: {auth['error'].get('message','')}"
        # Check balance - must be Demo account
        login_id = auth.get("authorize", {}).get("loginid", "")
        print(f"Deriv authorized as {login_id}")
        is_demo = "VRTC" in login_id or "demo" in login_id.lower()
        if not is_demo:
            print("WARNING: Not demo account! Blocking auto trade for safety.")
            ws.close()
            return "BLOCKED - NOT DEMO ACCOUNT - SAFETY STOP"

        deriv_symbol = DERIV_SYMBOLS.get(pair, "frxEURUSD")
        # Stake = 5% of Deriv balance, capped for demo safety
        # Use Multiplier trade - MULTUP for BUY, MULTDOWN for SELL
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        stake = 10  # $10 fixed for demo testing - safe
        # Proposal
        proposal = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 60,
            "duration_unit": "m",
            "symbol": deriv_symbol,
            "multiplier": 100,
            "limit_order": {
                "take_profit": {"order_amount": stake * 2},
                "stop_loss": {"order_amount": stake * 1}
            }
        }
        ws.send(json.dumps(proposal))
        prop_resp = json.loads(ws.recv())
        if "error" in prop_resp:
            print(f"Proposal error: {prop_resp['error']}")
            ws.close()
            return f"PROPOSAL FAILED: {prop_resp['error'].get('message','')}"
        proposal_id = prop_resp["proposal"]["id"]
        # Buy
        ws.send(json.dumps({"buy": proposal_id, "price": stake}))
        buy_resp = json.loads(ws.recv())
        ws.close()
        if "error" in buy_resp:
            print(f"Buy error: {buy_resp['error']}")
            return f"BUY FAILED: {buy_resp['error'].get('message','')}"
        contract_id = buy_resp.get("buy", {}).get("contract_id", "unknown")
        print(f"DERIV AUTO TRADE SUCCESS: {contract_id}")
        return f"AUTO TRADE PLACED - Contract ID: {contract_id} - Stake $10 - Multiplier x100"
    except Exception as e:
        print(f"Deriv trade error: {e}")
        return f"AUTO FAILED: {str(e)[:100]} - Do it manually"

def analyze(name, df):
    try:
        close = df["Close"]; high = df["High"]; low = df["Low"]; open_ = df["Open"]
        ema20 = close.ewm(span=20).mean(); ema50 = close.ewm(span=50).mean(); ema200 = close.ewm(span=200).mean()
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / (loss + 0.0000001))))
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        typical = (high + low + close) / 3
        vwap = typical.rolling(20).mean()
        ema12 = close.ewm(span=12).mean(); ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26; macd_sig = macd.ewm(span=9).mean()
        up = high.diff(); down = -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr_s = pd.Series(tr).rolling(14).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / (tr_s + 0.0000001))
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / (tr_s + 0.0000001))
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 0.0000001))
        adx = dx.rolling(14).mean()

        price = float(close.iloc[-1]); e20 = float(ema20.iloc[-1]); e50 = float(ema50.iloc[-1]); e200 = float(ema200.iloc[-1])
        r = float(rsi.iloc[-1]); a = float(atr.iloc[-1]); v = float(vwap.iloc[-1])
        m = float(macd.iloc[-1]); ms = float(macd_sig.iloc[-1]); adx_v = float(adx.iloc[-1])
        pip = PAIR_PIP[name]
        atr_pips = a / pip
        if atr_pips < 8: return None
        last_bull = float(close.iloc[-1]) > float(open_.iloc[-1])
        last_bear = float(close.iloc[-1]) < float(open_.iloc[-1])

        df4 = df.iloc[::16]
        c4 = df4["Close"]; e50_4 = c4.ewm(span=50).mean()
        trend4 = "BULLISH" if float(c4.iloc[-1]) > float(e50_4.iloc[-1]) else "BEARISH"
        dxy = get_dxy()
        if name in ["EURUSD", "XAUUSD"]:
            dxy_buy = dxy != "BULLISH"; dxy_sell = dxy != "BEARISH"
        else:
            dxy_buy = True; dxy_sell = True

        buy = [e20>e50 and e50>e200, trend4=="BULLISH", 45<r<68, price>v, m>ms and adx_v>25 and last_bull]
        sell = [e20<e50 and e50<e200, trend4=="BEARISH", 32<r<55, price<v, m<ms and adx_v>25 and last_bear]
        direction = None
        if sum(buy)==5 and dxy_buy: direction="BUY"
        elif sum(sell)==5 and dxy_sell: direction="SELL"
        else: return None

        sl_pips = max((a*1.5)/pip, MIN_SL_PIPS[name])
        sl_dist = sl_pips * pip
        if direction=="BUY":
            sl = price - sl_dist; tp1 = price + sl_dist; tp2 = price + sl_dist*2; tp3 = price + sl_dist*3
        else:
            sl = price + sl_dist; tp1 = price - sl_dist; tp2 = price - sl_dist*2; tp3 = price - sl_dist*3

        risk_money = BALANCE * 0.05
        if name=="XAUUSD": lot = round(risk_money/(sl_pips*1.0),2)
        else: lot = round(risk_money/(sl_pips*10),2)
        lot = max(0.01, min(lot, 5.0))

        # TRY AUTO TRADE FIRST
        auto_result = place_deriv_trade(name, direction, sl_pips, sl_pips*2)

        msg = (f"V7.0 AUTO - {name} {direction} - 1 TRADE TODAY\n"
               f"Price: {price:.5f}\n"
               f"Trend 4H: {trend4} | DXY: {dxy}\n"
               f"EMA20: {e20:.5f} EMA50: {e50:.5f} EMA200: {e200:.5f}\n"
               f"RSI: {r:.1f} | ADX: {adx_v:.1f} | MACD: {m:.5f}\n"
               f"VWAP: {v:.5f} | ATR: {atr_pips:.1f} pips\n"
               f"Entry: {price:.5f}\n"
               f"SL: {sl:.5f} ({sl_pips:.1f} pips)\n"
               f"TP1: {tp1:.5f}\nTP2: {tp2:.5f}\nTP3: {tp3:.5f}\n"
               f"Lot: {lot} | Risk: 5 pct | RR 1 to 3\n"
               f"DERIV STATUS: {auto_result}\n"
               f"Score: 5 per 5 - V6.4 BRAIN + AUTO\n"
               f"No more trades today. Bot locked.")
        return msg
    except Exception as e:
        print(f"Analyze error {name}: {e}")
        return None

def bot_loop():
    time.sleep(5)
    send("V7.0 AUTO is LIVE - DERIV DEMO AUTO TRADER\nV6.4 Sniper Brain + Auto Execution\n1 Trade Per DAY - Demo Only - Safety: Blocks if NOT Demo account\nIf Auto fails, Telegram signal still sent for manual")
    traded_date = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            today = now.date()
            if traded_date != today:
                traded_date = None
            if 7 <= now.hour < 20:
                if traded_date is None:
                    for name, ysym in SYMBOLS.items():
                        df = get_data(ysym)
                        if df is None: continue
                        sig = analyze(name, df)
                        if sig:
                            send(sig)
                            traded_date = today
                            break
                        time.sleep(3)
                else:
                    print(f"Already traded today {today}, locked")
            time.sleep(120)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(30)

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
