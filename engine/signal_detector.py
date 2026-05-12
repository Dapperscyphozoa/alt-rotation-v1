"""
alt-rotation-v1 — fires when BTC consolidates and an alt independently trends.

Edge identified via regime decoupling analysis:
  - Pairwise BTC↔alt regime agreement is 70-77% (tightly coupled in major moves)
  - BUT during BTC range periods (53% of time), 12-18% of alts independently
    trend, especially HYPE (only 53% BTC coupling, fully independent)
  - This is the alt-rotation pattern: when BTC consolidates, capital rotates
    into trending alts. The alt move is sustained because BTC isn't pulling
    them back yet.

Mechanic:
  1. Classify BTC regime per bar (SMA200 + ADX + 20-bar slope)
  2. Classify each alt's regime independently (same classifier)
  3. Fire LONG when:
       - BTC is in 'range' or 'chop'
       - This alt is in 'trend_up' (independent)
       - Alt's 20-bar slope > +2% (stronger than classifier threshold)
       - Alt closing above its session VWAP (momentum confirmation)
       - Volume on latest bar ≥ 1.2× avg
  4. Fire SHORT when:
       - BTC is in 'range' or 'chop'
       - This alt is in 'trend_down' (independent)
       - Alt's 20-bar slope < -2%
       - Alt closing below session VWAP
       - Volume ≥ 1.2× avg

SL: 1.5×ATR | TP: 4.0×ATR | max_hold: 12 bars (alt-rotation moves are fast)
"""
from __future__ import annotations
import json
import time
import urllib.request
import numpy as np
import pandas as pd
from typing import Optional
from .config import STRATEGY_PARAMS, TRADE_PARAMS

# BTC reference for regime check
_btc_cache = {"ts": 0, "regime": None}
_BTC_TTL = 1800   # 30 min


def _adx(highs, lows, closes, period=14):
    if len(highs) < 2: return np.full(len(highs), np.nan)
    tr = np.maximum.reduce([highs[1:]-lows[1:], np.abs(highs[1:]-closes[:-1]), np.abs(lows[1:]-closes[:-1])])
    plus_dm = np.where((highs[1:]-highs[:-1]) > (lows[:-1]-lows[1:]),
                        np.maximum(highs[1:]-highs[:-1], 0), 0)
    minus_dm = np.where((lows[:-1]-lows[1:]) > (highs[1:]-highs[:-1]),
                         np.maximum(lows[:-1]-lows[1:], 0), 0)
    atr = pd.Series(tr).ewm(span=period).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(span=period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period).mean() / atr
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)
    adx = dx.ewm(span=period).mean()
    return np.concatenate(([np.nan], adx.values))


def _classify_regime(df):
    """Per-bar regime: trend_up / trend_down / range / chop"""
    closes = df['close'].values
    if len(closes) < 200: return None
    sma200 = pd.Series(closes).rolling(200).mean().iloc[-1]
    slope_20 = pd.Series(closes).pct_change(20).iloc[-1]
    adx_arr = _adx(df['high'].values, df['low'].values, closes, 14)
    adx = adx_arr[-1]
    if pd.isna(sma200) or pd.isna(slope_20) or pd.isna(adx): return None

    above = closes[-1] > sma200
    trending = adx > 20
    if trending and above and slope_20 > 0.01: return 'trend_up'
    if trending and (not above) and slope_20 < -0.01: return 'trend_down'
    if adx < 15: return 'chop'
    return 'range'


def _get_btc_regime():
    """Cached BTC regime lookup. Fetch own candles if cache stale."""
    now = time.time()
    if now - _btc_cache["ts"] < _BTC_TTL and _btc_cache["regime"]:
        return _btc_cache["regime"]
    try:
        end_ms = int(now * 1000)
        start_ms = end_ms - 30 * 86400000   # 30 days for SMA200 on 1h
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
            data=json.dumps({"type":"candleSnapshot","req":{"coin":"BTC","interval":"1h",
                                                              "startTime":start_ms,"endTime":end_ms}}).encode(),
            headers={"Content-Type":"application/json"})
        raw = json.loads(urllib.request.urlopen(req, timeout=10).read())
        df = pd.DataFrame(raw)
        df['close'] = df['c'].astype(float)
        df['high'] = df['h'].astype(float)
        df['low'] = df['l'].astype(float)
        regime = _classify_regime(df)
        _btc_cache["ts"] = now
        _btc_cache["regime"] = regime
        return regime
    except Exception as e:
        print(f"[alt-rotation] btc fetch err: {e}", flush=True)
        return None


def _calc_atr(highs, lows, closes, period=14):
    h_s = pd.Series(highs); l_s = pd.Series(lows); pc = pd.Series(closes).shift(1)
    tr = pd.concat([h_s - l_s, (h_s - pc).abs(), (l_s - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]:
    """Fire alt-rotation signal when BTC consolidates + alt independently trends."""
    coin = df.attrs.get("coin", "")
    if not coin or coin == "BTC": return None   # Don't trade BTC itself
    if df is None or len(df) < 220: return None

    # Step 1: BTC must be in range or chop (not trending with this alt)
    btc_regime = _get_btc_regime()
    # In backtest mode, df might come with btc_regime injected via attrs
    if "btc_regime" in df.attrs:
        btc_regime = df.attrs["btc_regime"]
    if btc_regime not in ("range", "chop"):
        return None

    # Step 2: Alt's own regime must be a strong trend
    alt_regime = _classify_regime(df)
    if alt_regime not in ("trend_up", "trend_down"):
        return None

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    vols = df['volume'].values if 'volume' in df.columns else np.ones(len(df))
    last_c = float(closes[-1])

    # Step 3: Stronger slope requirement (we want clear independent trends)
    SLOPE_MIN = STRATEGY_PARAMS.get("alt_slope_min", 0.02)   # 2% over 20 bars
    slope_20 = (closes[-1] / closes[-21]) - 1 if len(closes) > 21 else 0
    is_long = alt_regime == "trend_up"
    if is_long and slope_20 < SLOPE_MIN: return None
    if (not is_long) and slope_20 > -SLOPE_MIN: return None

    # Step 4: VWAP momentum confirmation (last 24 bars)
    typical = (highs[-24:] + lows[-24:] + closes[-24:]) / 3
    vwap = (typical * vols[-24:]).sum() / vols[-24:].sum() if vols[-24:].sum() > 0 else last_c
    if is_long and last_c <= vwap: return None
    if (not is_long) and last_c >= vwap: return None

    # Step 5: Volume confirmation
    VOL_MULT = STRATEGY_PARAMS.get("vol_mult", 1.2)
    avg_vol = float(np.mean(vols[-20:-1]))
    if avg_vol > 0 and vols[-1] / avg_vol < VOL_MULT: return None

    # Step 6: Build trade
    atr = _calc_atr(highs, lows, closes, TRADE_PARAMS["atr_period"])
    if not atr or atr <= 0: return None

    sl_m = TRADE_PARAMS["sl_atr_mult"]
    tp_m = TRADE_PARAMS["tp_atr_mult"]
    if is_long:
        sl_p = last_c - sl_m * atr; tp_p = last_c + tp_m * atr
    else:
        sl_p = last_c + sl_m * atr; tp_p = last_c - tp_m * atr

    sl_pct = abs(last_c - sl_p) / last_c
    if sl_pct < 0.003 or sl_pct > 0.06: return None

    return {
        "fire_ts": df.index[-1], "ref_price": last_c, "atr": atr,
        "trade_side": "B" if is_long else "A", "is_long": is_long,
        "sl_px": float(sl_p), "tp_px": float(tp_p),
        "max_hold_bars": TRADE_PARAMS["max_hold_bars"],
        "fire_reason": f"alt_rot_btc{btc_regime}_alt{alt_regime}_slope{slope_20*100:.1f}pct",
        "raw_direction": "LONG" if is_long else "SHORT",
        "fade_direction": "LONG" if is_long else "SHORT",
        "btc_regime": btc_regime,
        "alt_regime": alt_regime,
        "slope_20": float(slope_20),
        "vwap_dist_pct": float((last_c - vwap) / vwap),
    }
