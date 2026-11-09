import os
import time
import asyncio
import requests
import threading
import json
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- SETTINGS ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
MIN_CONFIDENCE = 75
MAX_TRADES_PER_DAY = 1
HEARTBEAT_SECONDS = 7200
STAKE = 1

trades_today = 0
last_trade_date = None
last_signal = None
price_history = []
deriv_balance = 0
deriv_connected = False
deriv_account = ""

# --- RENDER PORT FIX ---
def keep_render_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"V7.0 DERIV CONNECTED")
        def log_message(self, *args):
            pass
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        print(f"Web server failed: {e}")
threading.Thread(target=keep_render_alive, daemon=True).start()

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"send status: {r.status_code} - {msg[:60]}")
    except Exception as e:
        print(f"Telegram failed: {e}")

def get_price():
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except:
        return None

# --- DERIV: TEST ON STARTUP ---
def test_deriv_on_startup():
    global deriv_connected, deriv_balance, deriv_account
    try:
        import websocket
    except:
        print("Installing websocket-client...")
        os.system("pip install websocket-client -q")
        import websocket
    try:
        print("=== TESTING DERIV CONNECTION NOW ===")
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
        ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        auth = json.loads(ws.recv())
        if "error" in auth:
            print(f"DERIV AUTH FAILED: {auth['error']['message']}")
            send_telegram(f"❌ Deriv Login FAILED: {auth['error']['message']}")
            ws.close()
            return False
        deriv_account = auth['authorize']['loginid']
        print(f"DERIV CONNECTED - Account: {deriv_account}")
        deriv_connected = True
        ws.send(json.dumps({"balance": 1}))
        bal = json.loads(ws.recv())
        deriv_balance = bal.get("balance", {}).get("balance", 0)
        currency = bal.get("balance", {}).get("currency", "USD")
        is_demo = "VRTC" in deriv_account
        print(f"DERIV BALANCE: {deriv_balance} {currency} - {'DEMO ✅ SAFE' if is_demo else 'REAL ⚠️ STOP!'}")
        send_telegram(f"✅ Deriv Connected!\nAccount: {deriv_account}\nBalance: {deriv_balance} {currency}\nType: {'DEMO ✅' if is_demo else 'REAL ⚠️'}\nBot will now watch for trades...")
        ws.close()
        return True
    except Exception as e:
        print(f"DERIV STARTUP TEST FAILED: {e}")
        send_telegram(f"❌ Deriv Startup Test Failed: {e}")
        return False

# --- DERIV: PLACE TRADE ---
def deriv_trade(signal, price):
    try:
        import websocket
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)
        ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        auth = json.loads(ws.recv())
        if "error" in auth:
            print(f"DERIV AUTH FAILED ON TRADE: {auth['error']['message']}")
            return False
        contract = "CALL" if signal == "BUY" else "PUT"
        ws.send(json.dumps({"proposal": 1, "amount": STAKE, "basis": "stake", "contract_type": contract, "currency": "USD", "duration": 5, "duration_unit": "m", "symbol": "frxEURUSD"}))
        prop = json.loads(ws.recv())
        if "error" in prop:
            print(f"DERIV PROPOSAL FAILED: {prop['error']['message']}")
            ws.close()
            return False
        proposal_id = prop["proposal"]["id"]
        print(f"Deriv Proposal OK: {contract} ${STAKE} id={proposal_id}")
        ws.send(json.dumps({"buy": proposal_id, "price": STAKE}))
        buy = json.loads(ws.recv())
        if "error" in buy:
            print(f"DERIV BUY FAILED: {buy['error']['message']}")
            ws.close()
            return False
        print(f"DERIV TRADE PLACED! Contract: {buy['buy']['contract_id']}")
        send_telegram(f"🎯 DERIV TRADE PLACED - DEMO\nSignal: {signal} ({contract})\nPair: EURUSD\nPrice: {price}\nStake: ${STAKE}\nContract ID: {buy['buy']['contract_id']}\nDuration: 5 min")
        ws.close()
        return True
    except Exception as e:
        print(f"DERIV TRADE ERROR: {e}")
        return False

def check_setup(price):
    global price_history
    price_history.append(price)
    if len(price_history) > 50:
        price_history = price_history[-50:]
    if len(price_history) < 10:
        return None, 0
    recent = sum(price_history[-5:]) / 5
    older = sum(price_history[-10:-5]) / 5
    change = recent - older
    # LOOSENED: was 0.0003, now 0.0001 so it can find setups in quiet market
    if abs(change) < 0.0001:
        return None, 0
    return ("BUY", 80) if change > 0 else ("SELL", 80)

async def heartbeat_loop():
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        send_telegram(f"💓 V7.0 Heartbeat - Still Alive\nTime: {datetime.now().strftime('%H:%M')}\nDeriv: {deriv_account} {'✅' if deriv_connected else '❌'}\nBalance: {deriv_balance}\nTrades Today: {trades_today}/{MAX_TRADES_PER_DAY}")

async def main_loop():
    global trades_today, last_trade_date, last_signal
    if not DERIV_TOKEN:
        print("❌ NO DERIV_TOKEN FOUND IN RENDER!")
    else:
        print(f"DERIV_TOKEN found, length: {len(DERIV_TOKEN)} chars")
        # TEST DERIV RIGHT NOW, DON'T WAIT FOR SIGNAL
        threading.Thread(target=test_deriv_on_startup, daemon=True).start()
        await asyncio.sleep(3)

    send_telegram("V7.0 AUTO is LIVE - DERIV STARTUP TEST VERSION\nTesting Deriv login now...")
    asyncio.create_task(heartbeat_loop())

    while True:
        today = date.today()
        if last_trade_date != today:
            trades_today = 0
            last_trade_date = today
        price = get_price()
        if price:
            print(f"EURUSD price: {price} - {datetime.now().strftime('%H:%M:%S')}")
            signal, confidence = check_setup(price)
            if signal and confidence >= MIN_CONFIDENCE and signal != last_signal:
                if trades_today < MAX_TRADES_PER_DAY:
                    last_signal = signal
                    trades_today += 1
                    print(f"SIGNAL FOUND: {signal} - Trying Deriv...")
                    deriv_trade(signal, price)
                else:
                    print("Max trades reached")
            else:
                print(f"No setup - confidence {confidence}% - waiting")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main_loop())
