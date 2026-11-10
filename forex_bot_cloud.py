import os, time, requests, datetime, pytz
from datetime import datetime as dt
import MetaTrader5 as mt5

# ============ CONFIG ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
TIMEFRAME_4H = mt5.TIMEFRAME_H4
TIMEFRAME_1H = mt5.TIMEFRAME_H1
TIMEFRAME_15M = mt5.TIMEFRAME_M15

# Killzones in Nairobi time (UTC+3)
KILLZONES = [(10, 12), (15, 18)]  # London 10-12, NY 15-18
MAGIC = 0

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def in_killzone():
    tz = pytz.timezone("Africa/Nairobi")
    now = dt.now(tz)
    for start, end in KILLZONES:
        if start <= now.hour < end:
            return True, now
    return False, now

def get_rates(symbol, tf, n=250):
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    return rates

def ema(values, period):
    k = 2 / (period + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val

def check_spread(symbol):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        return False
    spread_points = (tick.ask - tick.bid) / info.point
    # Sharper: skip high spread
    if "XAU" in symbol and spread_points > 35:
        return False
    if "EUR" in symbol and spread_points > 20:
        return False
    if "GBP" in symbol and spread_points > 25:
        return False
    if "JPY" in symbol and spread_points > 25:
        return False
    return True

def analyze(symbol):
    # --- Check spread first ---
    if not check_spread(symbol):
        print(f"{symbol}: Spread too high, skip")
        return None

    rates_4h = get_rates(symbol, TIMEFRAME_4H, 100)
    rates_1h = get_rates(symbol, TIMEFRAME_1H, 100)
    rates_15m = get_rates(symbol, TIMEFRAME_15M, 100)
    if rates_4h is None or rates_1h is None or rates_15m is None:
        return None

    closes_4h = [r['close'] for r in rates_4h]
    closes_1h = [r['close'] for r in rates_1h]
    closes_15m = [r['close'] for r in rates_15m]

    # --- SHARPER RULE 3: EMA 50 + EMA 200 BOTH must agree ---
    ema50_4h = ema(closes_4h, 50)
    ema200_4h = ema(closes_4h, 200)
    price_4h = closes_4h[-1]

    trend = None
    if price_4h > ema50_4h and price_4h > ema200_4h:
        trend = "BUY"
    elif price_4h < ema50_4h and price_4h < ema200_4h:
        trend = "SELL"
    else:
        print(f"{symbol}: Price between EMAs - chop zone, skip")
        return None

    # --- 1H BOS ---
    # Simple BOS: last 1H close breaks previous 1H high/low
    high_1h_prev = max([r['high'] for r in rates_1h[-20:-1]])
    low_1h_prev = min([r['low'] for r in rates_1h[-20:-1]])
    last_1h = rates_1h[-1]

    bos_buy = last_1h['close'] > high_1h_prev
    bos_sell = last_1h['close'] < low_1h_prev

    if trend == "BUY" and not bos_buy:
        return None
    if trend == "SELL" and not bos_sell:
        return None

    # --- SHARPER RULE 2: Liquidity Sweep with Wick Rejection ---
    # Look back 15m: sweep previous low/high then close back inside
    prev_low_15m = min([r['low'] for r in rates_15m[-20:-2]])
    prev_high_15m = max([r['high'] for r in rates_15m[-20:-2]])
    last_15m = rates_15m[-1]
    prev_15m = rates_15m[-2]

    sweep_buy = False
    sweep_sell = False

    # For BUY: price wicked below prev low, then closed back above it
    if trend == "BUY":
        if prev_15m['low'] < prev_low_15m and prev_15m['close'] > prev_low_15m:
            # Wick rejection: wick is at least 60% of candle
            candle_range = prev_15m['high'] - prev_15m['low']
            lower_wick = prev_15m['close'] - prev_15m['low']
            if candle_range > 0 and lower_wick / candle_range > 0.6:
                sweep_buy = True
    if trend == "SELL":
        if prev_15m['high'] > prev_high_15m and prev_15m['close'] < prev_high_15m:
            candle_range = prev_15m['high'] - prev_15m['low']
            upper_wick = prev_15m['high'] - prev_15m['close']
            if candle_range > 0 and upper_wick / candle_range > 0.6:
                sweep_sell = True

    if trend == "BUY" and not sweep_buy:
        print(f"{symbol}: No wick sweep for BUY, skip")
        return None
    if trend == "SELL" and not sweep_sell:
        print(f"{symbol}: No wick sweep for SELL, skip")
        return None

    # --- SHARPER RULE 1: FVG + Retest ---
    # Find FVG in last 10 candles of 15m, price must be retesting it NOW
    fvg_found = False
    fvg_top = fvg_bottom = 0
    for i in range(len(rates_15m)-10, len(rates_15m)-2):
        c1 = rates_15m[i]
        c3 = rates_15m[i+2]
        # Bullish FVG: c1 high < c3 low
        if trend == "BUY" and c1['high'] < c3['low']:
            fvg_bottom = c1['high']
            fvg_top = c3['low']
            fvg_found = True
            break
        # Bearish FVG: c1 low > c3 high
        if trend == "SELL" and c1['low'] > c3['high']:
            fvg_top = c1['low']
            fvg_bottom = c3['high']
            fvg_found = True
            break

    if not fvg_found:
        return None

    # Retest check: current price is inside FVG zone
    current_price = last_15m['close']
    if not (fvg_bottom <= current_price <= fvg_top or abs(current_price - fvg_bottom) < (fvg_top - fvg_bottom)*0.3):
        print(f"{symbol}: Price not retesting FVG yet, waiting...")
        return None

    # --- Calculate Entry / SL / TP ---
    entry = current_price
    if trend == "BUY":
        sl = prev_low_15m - (fvg_top - fvg_bottom)  # below sweep low
        risk = entry - sl
        tp = entry + risk * 2  # 1:2 RR
    else:
        sl = prev_high_15m + (fvg_top - fvg_bottom)
        risk = sl - entry
        tp = entry - risk * 2

    if risk <= 0:
        return None

    return {
        "symbol": symbol,
        "trend": trend,
        "entry": round(entry, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "rr": "1:2",
        "fvg": f"{round(fvg_bottom,5)} - {round(fvg_top,5)}"
    }

def main():
    if not mt5.initialize():
        print("MT5 init failed")
        return
    send_telegram("✅ *V13 SHARPER is LIVE*\nUnlimited signals, but ONLY A+ setups.\nEMA 50+200 + Wick Sweep + FVG Retest + Spread Filter")
    print("V13 Started")

    sent_today = set()  # to avoid duplicate signal for same candle

    while True:
        ok, now = in_killzone()
        if not ok:
            print(f"Outside killzone {now}, sleeping 120s")
            time.sleep(120)
            continue

        for sym in SYMBOLS:
            try:
                mt5.symbol_select(sym, True)
                result = analyze(sym)
                if result:
                    key = f"{sym}-{result['trend']}-{result['entry']}"
                    if key in sent_today:
                        continue
                    sent_today.add(key)
                    # NO MAX LIMIT - send whenever parameters align
                    msg = (
                        f"🔥 *V13 SHARPER SIGNAL*\n\n"
                        f"Pair: {result['symbol']}\n"
                        f"Direction: {result['trend']}\n"
                        f"Entry: {result['entry']}\n"
                        f"SL: {result['sl']}\n"
                        f"TP: {result['tp']}\n"
                        f"RR: {result['rr']}\n"
                        f"FVG Zone: {result['fvg']}\n"
                        f"Filters: EMA50+200 ✅ Wick Sweep ✅ Retest ✅ Spread ✅\n\n"
                        f"Lot: 0.01 ONLY\n"
                        f"Set SL + TP in MT5!"
                    )
                    send_telegram(msg)
                    print(f"Signal sent {sym} {result['trend']}")
            except Exception as e:
                print(f"Error {sym}: {e}")

        time.sleep(60)  # check every 60s

if __name__ == "__main__":
    main()
