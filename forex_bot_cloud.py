def check_pair(pair):
    try:
        df_1h = get_data(pair, "1h")
        df_15m = get_data(pair, "15m")
        if df_1h is None or df_15m is None:
            print(f"{pair}: Score 0/70 - No data - skipping")
            return None

        # --- Get last closed candles ---
        c1h = df_1h.iloc[-2]
        c15 = df_15m.iloc[-2]
        prev_15 = df_15m.iloc[-3]

        score = 0
        reasons = []

        # 1. EMA TREND - 20 points
        ema50 = df_1h['close'].ewm(span=50).mean().iloc[-2]
        ema200 = df_1h['close'].ewm(span=200).mean().iloc[-2]
        ema_ok = False
        direction = None

        if c1h['close'] > ema50 > ema200:
            ema_ok = True
            direction = "BUY"
            score += 20
        elif c1h['close'] < ema50 < ema200:
            ema_ok = True
            direction = "SELL"
            score += 20
        else:
            reasons.append("EMA fail")

        # 2. BOS - Break of Structure - 25 points
        bos_ok = False
        last_high = df_1h['high'].iloc[-4:-1].max()
        last_low = df_1h['low'].iloc[-4:-1].min()

        if direction == "BUY" and c1h['close'] > last_high:
            bos_ok = True
            score += 25
        elif direction == "SELL" and c1h['close'] < last_low:
            bos_ok = True
            score += 25
        else:
            reasons.append("No BOS")

        # 3. FVG - Fair Value Gap on 15m - 25 points
        fvg_ok = False
        # Simple FVG check: big candle with gap
        if direction == "BUY":
            # Bullish FVG: low of current > high of 2 candles ago
            if c15['low'] > df_15m.iloc[-4]['high']:
                fvg_ok = True
                score += 25
            else:
                reasons.append("No FVG")
        elif direction == "SELL":
            if c15['high'] < df_15m.iloc[-4]['low']:
                fvg_ok = True
                score += 25
            else:
                reasons.append("No FVG")
        else:
            reasons.append("No direction")

        # --- ALWAYS PRINT SCORE ---
        reason_str = ", ".join(reasons) if reasons else "All pass"
        print(f"{pair}: Score {score}/70 - {reason_str} - {'SIGNAL!' if score >= 70 else 'skipping'}", flush=True)

        if score < 70:
            return None

        # --- If 70/70, build signal ---
        entry = c15['close']
        atr = df_1h['high'].iloc[-14:].max() - df_1h['low'].iloc[-14:].min()
        atr = atr / 14 if atr else entry * 0.002

        if direction == "BUY":
            sl = entry - atr * 1.5
            tp = entry + atr * 3.0
        else:
            sl = entry + atr * 1.5
            tp = entry - atr * 3.0

        return {
            "pair": pair,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "score": score
        }

    except Exception as e:
        print(f"{pair}: Score 0/70 - Error {e} - skipping")
        return None
