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
STAKE = 1  # $1 DEMO ONLY

trades_today = 0
last_trade_date = None
last_signal = None
price_history = []
deriv_balance = 0
deriv_connected = False

# --- FIX RENDER PORT ---
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
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"send status: 200 - {msg[:60]}")
    except Exception as e:
        print(f"Telegram failed: {e}")

def get_price():
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except:
        return None

# --- DERIV CONNECTION ---
def deriv_trade(signal, price):
    global deriv_connected, deriv_balance
    try:
        import websocket
    except:
        print("Installing websocket-client...")
        os.system("pip install websocket-client -q")
        import websocket

    try:
        print("Connecting to Deriv...")
        ws = websocket.create_connection("wss://ws.derivws.com/websockets/v3?app_id=1089", timeout=15)

        # 1. AUTHORIZE
        ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        auth = json.loads(ws.recv())
        if "error" in auth:
            print(f"DERIV AUTH FAILED: {auth['error']['message']}")
            send_telegram(f"❌ Deriv Login FAILED: {auth['error']['message']}\nCheck your DERIV_TOKEN - is it Demo token?")
            return False

        print(f"DERIV CONNECTED - Account: {auth['authorize']['loginid']}")
        deriv_connected = True

        # 2. GET BALANCE
        ws.send(json.dumps({"balance": 1, "subscribe": 0}))
        bal = json.loads(ws.recv())
        deriv_balance = bal.get("balance", {}).get("balance", 0)
        currency = bal.get("balance", {}).get("currency", "USD")
        print(f"DERIV BALANCE: {deriv_balance} {currency} - {'DEMO' if 'VRTC' in auth['authorize']['loginid'] else 'REAL - STOP!'}")
        send_telegram(f"✅ Deriv Connected!\nAccount: {auth['authorize']['loginid']}\nBalance: {deriv_balance} {currency}\nNow watching for trade...")

        # 3. PROPOSAL - Rise/Fall on EURUSD, 5 min, $1 Demo
        contract = "CALL" if signal == "BUY" else "PUT"
        ws.send(json.dumps({
            "proposal": 1,
            "amount": STAKE,
            "basis": "stake",
            "contract_type": contract,
            "currency": "USD",
            "duration": 5,
            "duration_unit": "m",
            "symbol": "frxEURUSD"
        }))
        prop = json.loads(ws.recv())
        if "error" in prop:
            print(f"DERIV PROPOSAL FAILED: {prop['error']['message']}")
            send_telegram(f"❌ Deriv Proposal Failed: {prop['error']['message']}")
            ws.close()
            return False

        proposal_id = prop["proposal"]["id"]
        print(f"Deriv Proposal OK: {contract} ${STAKE} id={proposal_id}")

        # 4. BUY
        ws.send(json.dumps({"buy": proposal_id, "price": STAKE}))
        buy = json.loads(ws.recv())
        if "error" in buy:
            print(f"DERIV BUY FAILED: {buy['error']['message']}")
            send_telegram(f"❌ Deriv Buy Failed: {buy['error']['message']}")
            ws.close()
            return False

        print(f"DERIV TRADE PLACED! Contract: {buy['buy']['contract_id']}")
        send_telegram(f"🎯 DERIV TRADE PLACED - DEMO\nSignal: {signal} ({contract})\nPair: EURUSD\nPrice: {price}\nStake: ${STAKE}\nContract ID: {buy['buy']['contract_id']}\nDuration: 5 min")
        ws.close()
        return True

    except Exception as e:
        print(f"DERIV ERROR: {e}")
        send_telegram(f"❌ Deriv Error: {e}")
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
    if abs(change) < 0.0003:
        return None, 0
    return ("BUY", 80) if change > 0 else ("SELL", 80)

async def heartbeat_loop():
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        send_telegram(f"💓 V7.0 Heartbeat - Still Alive\nTime: {datetime.now().strftime('%H:%M')}\nDeriv: {'Connected' if deriv_connected else 'Not yet - waiting for first trade'}\nBalance: {deriv_balance}\nTrades Today: {trades_today}/{MAX_TRADES_PER_DAY}")

async def main_loop():
    global trades_today, last_trade_date, last_signal
    if not DERIV_TOKEN:
        print("❌ NO DERIV_TOKEN FOUND IN RENDER!")
        send_telegram("❌ No DERIV_TOKEN in Render Environment! Add it!")
    else:
        print(f"DERIV_TOKEN found, length: {len(DERIV_TOKEN)} chars")

    send_telegram("V7.0 AUTO is LIVE - DERIV DEMO CONNECTED VERSION\nWill login to Deriv on first signal to test balance")
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
