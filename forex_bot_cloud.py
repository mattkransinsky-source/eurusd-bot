import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
}
CORR_GROUP = {"EURUSD": "USD", "GBPUSD": "USD", "XAUUSD": "USD", "USDJPY": "JPY"}
SYMBOLS = {v: k for k, v in PAIRS.items()}

def now_eat():
    return datetime.now() + timedelta(hours=3)

def in_session():
    now = now_eat()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return 10 <= now.hour < 21

# --- UPGRADE 3: NEWS FILTER ---
def in_news_block():
    now = now_eat()
    # Block 15:15 - 16:30 EAT on Wed (CPI) and Fri (NFP) + Tue 15:30 US data
    if now.weekday() in [2, 4]:  # Wed, Fri
        if 15 <= now.hour < 17:
            return True
    if now.weekday() == 1 and 15 <= now.hour < 16:  # Tue US data
        return True
    return False

def send_msg(msg):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"TG error: {e}")
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        except: pass

# --- INDICATORS ---
def calc_ema(df, span):
    return df['Close'].ewm(span=span, adjust=False).mean().iloc[-1]

def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 0.0000001)
    return 100 - (100 / (1 + rs)).iloc[-1]

def calc_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def calc_adx(df, period=14):
    # True ADX - measures trend strength
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    plus_di = 100 * (plus_dm.rolling(period).mean() / (atr + 0.0000001))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr + 0.0000001))
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 0.0000001))
    return dx.rolling(period).mean().iloc[-1]

# --- UPGRADE 2: BOS + FVG ---
def check_bos_fvg(df):
    # BOS: Last 2 candles broke last swing high/low (last 20 candles)
    closes = df['Close']
    highs = df['High']
    lows = df['Low']
    swing_high = highs.iloc[-21:-1].max()
    swing_low = lows.iloc[-21:-1].min()
    last_close = closes.iloc[-1]
    prev_close = closes.iloc[-2]
    bos_up = prev_close < swing_high and last_close > swing_high
    bos_down = prev_close > swing_low and last_close < swing_low

    # FVG: Gap between candle -3 low and candle -1 high (bullish) or vice versa
    # Bullish FVG: Low[-3] > High[-1] means gap up
    fvg_bull = lows.iloc[-3] > highs.iloc[-1]
    fvg_bear = highs.iloc[-3] < lows.iloc[-1]
    # Also check recent 5 candles for any FVG
    for i in range(-6, -2):
        if lows.iloc[i] > highs.iloc[i+2]:
            fvg_bull = True
        if highs.iloc[i] < lows.iloc[i+2]:
            fvg_bear = True
    return bos_up, bos_down, fvg_bull, fvg_bear, swing_high, swing_low

def get_signal(name, ticker):
    try:
        # 15M data
        df15 = yf.download(ticker, period="5d", interval="15m", progress=False, auto_adjust=False)
        if df15 is None or len(df15) < 210:
            return None
        if isinstance(df15.columns, pd.MultiIndex):
            df15.columns = df15.columns.get_level_values(0)
        df15 = df15.dropna()
        # 1H data for trend filter
        df1h = yf.download(ticker, period="10d", interval="1h", progress=False, auto_adjust=False)
        if df1h is not None and len(df1h) > 210:
            if isinstance(df1h.columns, pd.MultiIndex):
                df1h.columns = df1h.columns.get_level_values(0)
            df1h = df1h.dropna()
            ema200_1h = calc_ema(df1h, 200)
            ema50_1h = calc_ema(df1h, 50)
        else:
            ema200_1h = ema50_1h = None

        price = float(df15['Close'].iloc[-1])
        ema50 = calc_ema(df15, 50)
        ema200 = calc_ema(df15, 200)
        rsi = calc_rsi(df15['Close'])
        atr = calc_atr(df15)
        adx = calc_adx(df15)
        bos_up, bos_down, fvg_bull, fvg_bear, sh, sl = check_bos_fvg(df15)

        # Base 15M signal
        signal = None
        if price > ema50 > ema200 and 45 < rsi < 68:
            signal = "BUY"
        elif price < ema50 < ema200 and 32 < rsi < 55:
            signal = "SELL"
        if not signal:
            return None

        # --- UPGRADE 1: 1H + ADX FILTER ---
        # Must have ADX > 20 = real trend, not chop
        if adx < 20:
            print(f"{name} blocked: ADX {adx:.1f} too low (chop)")
            return None
        # 1H must agree with 15M
        if ema50_1h is not None:
            if signal == "BUY" and not (price > ema50_1h > ema200_1h):
                print(f"{name} blocked: 1H trend not BUY")
                return None
            if signal == "SELL" and not (price < ema50_1h < ema200_1h):
                print(f"{name} blocked: 1H trend not SELL")
                return None

        # --- UPGRADE 2: BOS REQUIRED, FVG BONUS ---
        if signal == "BUY" and not bos_up:
            print(f"{name} blocked: No BOS up")
            return None
        if signal == "SELL" and not bos_down:
            print(f"{name} blocked: No BOS down")
            return None

        has_fvg = (fvg_bull if signal == "BUY" else fvg_bear)
        quality = "⭐⭐⭐ A+ (BOS + FVG)" if has_fvg else "⭐⭐ A (BOS only)"

        # ATR TP/SL
        if signal == "BUY":
            sl_price = price - atr
            tp_price = price + atr * 2
        else:
            sl_price = price + atr
            tp_price = price - atr * 2

        return {
            "pair": name, "signal": signal, "price": price,
            "sl": sl_price, "tp": tp_price, "rsi": rsi, "atr": atr,
            "adx": adx, "quality": quality, "fvg": has_fvg
        }
    except Exception as e:
        print(f"Error {name}: {e}")
        return None

def main():
    print("V10 Manual Smarter Started")
    send_msg("🚀 *V10 MANUAL SMARTER is LIVE*\n\nUpgrades:\n✅ 1H Trend + ADX > 20 filter\n✅ BOS Required + FVG Bonus\n✅ News Block (Wed/Fri 15:30 EAT)\n✅ Session 10:00-21:00 EAT\n✅ ATR TP/SL\n\nExpect 1-2 signals/day, but sharper.")
    last_signals = {}
    sent_this_hour = {}
    while True:
        try:
            now = now_eat()
            hour_key = now.strftime("%Y-%m-%d-%H")
            if hour_key not in sent_this_hour:
                sent_this_hour[hour_key] = []

            if in_news_block():
                print(f"News block - sleeping {now.strftime('%H:%M')}")
                time.sleep(300)
                continue

            if not in_session():
                time.sleep(60)
                continue

            for name, ticker in PAIRS.items():
                res = get_signal(name, ticker)
                if not res:
                    continue
                if last_signals.get(name) == res["signal"]:
                    continue
                # Correlation Block
                group = CORR_GROUP[name]
                if any(g == group and d == res["signal"] for g, d in sent_this_hour[hour_key]):
                    print(f"{name} blocked by correlation")
                    continue

                last_signals[name] = res["signal"]
                sent_this_hour[hour_key].append((group, res["signal"]))
                fvg_text = "✅ FVG Found" if res["fvg"] else "No FVG (BOS only)"

                msg = (
                    f"🚨 *V10 SMARTER SIGNAL*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*Pair:* {res['pair']}\n"
                    f"*Signal:* {res['signal']} {'🟢' if res['signal']=='BUY' else '🔴'}\n"
                    f"*Quality:* {res['quality']}\n"
                    f"*Entry:* `{res['price']:.5f}`\n"
                    f"*Stop Loss:* `{res['sl']:.5f}`\n"
                    f"*Take Profit:* `{res['tp']:.5f}`\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"*RSI:* {res['rsi']:.1f}\n"
                    f"*ADX:* {res['adx']:.1f} (Trend Strength)\n"
                    f"*ATR:* {res['atr']:.5f}\n"
                    f"*FVG:* {fvg_text}\n"
                    f"*BOS:* ✅ Confirmed\n"
                    f"*1H Trend:* ✅ Agrees\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"⏰ {now.strftime('%H:%M')} EAT\n"
                    f"⚠️ Valid 30 mins | 1% Risk | Manual Only"
                )
                send_msg(msg)
                print(f"Sent {name} {res['signal']}")
            time.sleep(300)  # 5 min loop
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
