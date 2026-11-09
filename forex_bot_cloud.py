import os
import asyncio
import requests
import threading
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

price_history = []
last_signal = None
signals_today = 0
last_signal_date = None

def keep_render_alive():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SIGNAL BOT LIVE")
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
        if r.status_code == 404:
            print("TELEGRAM 404: You blocked the bot or Chat ID is wrong. Unblock bot and check TELEGRAM_CHAT_ID")
        if r.status_code == 400:
            print(f"TELEGRAM 400: {r.text}")
        return r.status_code
    except Exception as e:
        print(f"Telegram failed: {e}")
        return 0

def get_price():
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception as e:
        print(f"Price failed: {e}")
        return None

def check_signal(price):
    global price_history
    price_history.append(price)
    if len(price_history) > 50:
        price_history = price_history[-50:]
    if len(price_history) < 10:
        return None, 0
    recent = sum(price_history[-5:]) / 5
    older = sum(price_history[-10:-5]) / 5
    change = recent - older
    # Quiet market = need only 0.0001 move
    if abs(change) < 0.0001:
        return None, 0
    return ("BUY", 80) if change > 0 else ("SELL", 80)

async def heartbeat():
    while True:
        await asyncio.sleep(7200) # every 2 hours
        send_telegram(f"💓 Signal Bot Still Alive\nTime: {datetime.now().strftime('%H:%M')}\nSignals Today: {signals_today}\nLast Signal: {last_signal}")

async def main():
    global last_signal, signals_today, last_signal_date
    print(f"TELEGRAM_TOKEN set: {bool(TELEGRAM_TOKEN)}")
    print(f"TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")
    # TEST TELEGRAM RIGHT NOW
    test = send_telegram("✅ Signal Bot is LIVE - Signals Only, NO Auto Trading\nYou will get BUY / SELL signals here.\nIf you see this, Telegram is FIXED.")
    if test != 200:
        print("TELEGRAM TEST FAILED - Fix Chat ID / Unblock bot")

    asyncio.create_task(heartbeat())
    while True:
        today = date.today()
        if last_signal_date != today:
            signals_today = 0
            last_signal_date = today
            last_signal = None

        price = get_price()
        if price:
            print(f"EURUSD price: {price} - {datetime.now().strftime('%H:%M:%S')}")
            signal, conf = check_signal(price)
            if signal and signal != last_signal:
                last_signal = signal
                signals_today += 1
                if signal == "BUY":
                    tp = price + 0.0020
                    sl = price - 0.0010
                    emoji = "🟢"
                else:
                    tp = price - 0.0020
                    sl = price + 0.0010
                    emoji = "🔴"
                msg = f"{emoji} SIGNAL - EURUSD {signal}\n\nEntry: {price:.4f}\nTake Profit: {tp:.4f}\nStop Loss: {sl:.4f}\nConfidence: {conf}%\nTime: {datetime.now().strftime('%H:%M')}\n\n⚠️ Signals only - YOU decide to trade. Bot will NOT trade for you."
                send_telegram(msg)
                print(f"SIGNAL SENT: {signal} at {price}")
            else:
                print(f"No setup - waiting - last: {last_signal}")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
