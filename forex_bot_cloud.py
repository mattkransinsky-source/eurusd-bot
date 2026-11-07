import time, requests, os

# --- Telegram from Render Environment ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not set - skipping")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        print("Telegram sent!")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_price():
    try:
        # Free price from exchangerate
        r = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10)
        data = r.json()
        return data["rates"]["USD"]
    except:
        return 1.11433

print("EURUSD Bot with Telegram is LIVE")
send_telegram("✅ EURUSD Bot is LIVE - You will get STRONG signals here")

last_signal = "HOLD"
prices = []

while True:
    price = get_price()
    prices.append(price)
    if len(prices) > 50:
        prices.pop(0)

    fast = sum(prices[-10:]) / len(prices[-10:])
    slow = sum(prices) / len(prices)
    long_confirm = sum(prices[-20:]) / len(prices[-20:]) if len(prices) >= 20 else slow

    signal = "HOLD"
    reason = "too flat / not confirmed"

    if fast > slow and fast > long_confirm:
        signal = "BUY"
        reason = "STRONG"
    elif fast < slow and fast < long_confirm:
        signal = "SELL"
        reason = "STRONG"

    print(f"EURUSD: {price:.5f} | Fast: {fast:.5f} Slow: {slow:.5f} Long: {long_confirm:.5f} -> {signal} - {reason}", flush=True)

    # Only send Telegram when signal CHANGES to BUY/SELL, not every minute
    if signal in ["BUY", "SELL"] and signal != last_signal:
        send_telegram(f"🚨 EURUSD {signal} signal - STRONG\nPrice: {price:.5f}\nFast: {fast:.5f} Slow: {slow:.5f}\nDemo: 0.01 lot, SL 20 pips, TP 40 pips")

    if signal in ["BUY", "SELL"]:
        last_signal = signal
    else:
        # Reset after HOLD so next BUY/SELL will send again
        if len(prices) > 5:
            last_signal = "HOLD"

    time.sleep(60)
