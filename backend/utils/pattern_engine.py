"""
Fix 3: Lower confidence floor 0.6 → 0.5 and add RSI + MACD momentum signals
so that the pattern layer fires even when geometric formations are absent.

All pattern detection functions return:
    {"pattern": str, "confidence": float, "direction": "bullish"|"bearish"|"neutral"}

detect_pattern() collects every candidate above the confidence floor and
returns the one with the highest confidence.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Fix 3: lowered from 0.6 to 0.5
_CONFIDENCE_FLOOR = 0.5


def detect_pattern(ohlc: pd.DataFrame) -> dict:
    """
    Detect chart patterns (geometric + momentum) from OHLCV data.

    Returns:
        dict: {"pattern": str, "confidence": float, "direction": str}
    """
    _none = {"pattern": "none", "confidence": 0.0, "direction": "neutral"}

    try:
        if ohlc is None or ohlc.empty or len(ohlc) < 30:
            logger.warning("[PATTERN] Insufficient data for pattern detection")
            return _none

        required = ["open", "high", "low", "close", "volume"]
        if not all(col in ohlc.columns for col in required):
            logger.error("[PATTERN] Missing required OHLCV columns")
            return _none

        close  = ohlc["close"]
        high   = ohlc["high"]
        low    = ohlc["low"]
        volume = ohlc["volume"]

        if volume.sum() == 0:
            logger.warning("[PATTERN] All-zero volume — skipping pattern detection")
            return _none

        # Collect all candidates above floor
        candidates = []

        # --- Geometric patterns ---
        for detect_fn, direction in [
            (_detect_double_top,          "bearish"),
            (_detect_head_and_shoulders,  "bearish"),
            (_detect_ascending_triangle,  "bullish"),
        ]:
            result = detect_fn(close, high, low, volume)
            if result["pattern"] != "none" and result["confidence"] >= _CONFIDENCE_FLOOR:
                candidates.append({
                    "pattern":    result["pattern"],
                    "confidence": result["confidence"],
                    "direction":  direction,
                })

        # --- Momentum signals (Fix 3: new) ---
        for detect_fn in [_detect_rsi_signal, _detect_macd_crossover]:
            result = detect_fn(close, high, low, volume)
            if result["pattern"] != "none" and result["confidence"] >= _CONFIDENCE_FLOOR:
                candidates.append(result)

        if not candidates:
            logger.info("[PATTERN] No pattern detected above confidence floor")
            return _none

        # Return highest-confidence candidate
        best = max(candidates, key=lambda c: c["confidence"])
        logger.info(f"[PATTERN] Detected {best['pattern']} ({best['direction']}) "
                    f"conf={best['confidence']:.2f}")
        return best

    except Exception as e:
        logger.error(f"[PATTERN] Error in pattern detection: {str(e)}")
        return _none


# ---------------------------------------------------------------------------
# Geometric pattern detectors
# ---------------------------------------------------------------------------

def _detect_double_top(close, high, low, volume) -> dict:
    """
    Two peaks within ±2%, valley ≥ 3% below, volume lower on second peak.
    """
    try:
        window = 5
        peaks  = (high == high.rolling(window=window, center=True).max()) & \
                 (high.rolling(window=window, center=True).count() == window)
        peak_indices = high[peaks].index.tolist()

        if len(peak_indices) < 2:
            return {"pattern": "none", "confidence": 0.0}

        p1_idx, p2_idx = peak_indices[-2], peak_indices[-1]
        if p1_idx > p2_idx:
            p1_idx, p2_idx = p2_idx, p1_idx

        p1, p2 = float(high.loc[p1_idx]), float(high.loc[p2_idx])

        peak_diff_pct = abs(p1 - p2) / p1
        if peak_diff_pct > 0.02:
            return {"pattern": "none", "confidence": 0.0}

        valley     = float(low.loc[p1_idx:p2_idx].min())
        valley_pct = (min(p1, p2) - valley) / min(p1, p2)
        if valley_pct < 0.03:
            return {"pattern": "none", "confidence": 0.0}

        vol_conf = 1.0 if float(volume.loc[p2_idx]) < float(volume.loc[p1_idx]) else 0.7

        peak_conf   = 1.0 - (peak_diff_pct / 0.02)
        valley_conf = min(1.0, valley_pct / 0.03)
        confidence  = (peak_conf + valley_conf) / 2 * vol_conf
        confidence  = float(np.clip(confidence, 0.0, 1.0))

        return {"pattern": "double_top", "confidence": confidence}

    except Exception as e:
        logger.error(f"[PATTERN] double_top error: {e}")
        return {"pattern": "none", "confidence": 0.0}


def _detect_ascending_triangle(close, high, low, volume) -> dict:
    """
    Flat resistance (slope ≈ 0, ≤ 0.1%) + rising support (slope > 0).
    """
    try:
        lookback = min(30, len(close))
        h_sub    = high.iloc[-lookback:]
        l_sub    = low.iloc[-lookback:]
        window   = 4

        res_peaks   = (h_sub == h_sub.rolling(window=window, center=True).max()) & \
                      (h_sub.rolling(window=window, center=True).count() == window)
        sup_troughs = (l_sub == l_sub.rolling(window=window, center=True).min()) & \
                      (l_sub.rolling(window=window, center=True).count() == window)

        res_idx = h_sub[res_peaks].index.tolist()
        sup_idx = l_sub[sup_troughs].index.tolist()

        if len(res_idx) < 2 or len(sup_idx) < 2:
            return {"pattern": "none", "confidence": 0.0}

        y_res     = np.array(h_sub.loc[res_idx].tolist())
        slope_res = np.polyfit(np.arange(len(y_res)), y_res, 1)[0]
        slope_res_norm = abs(slope_res / np.mean(y_res)) if np.mean(y_res) != 0 else float("inf")
        if slope_res_norm > 0.001:
            return {"pattern": "none", "confidence": 0.0}

        y_sup     = np.array(l_sub.loc[sup_idx].tolist())
        slope_sup = np.polyfit(np.arange(len(y_sup)), y_sup, 1)[0]
        slope_sup_norm = slope_sup / np.mean(y_sup) if np.mean(y_sup) != 0 else float("inf")
        if slope_sup_norm <= 0:
            return {"pattern": "none", "confidence": 0.0}

        res_conf   = max(0.0, 1.0 - (slope_res_norm / 0.001))
        sup_conf   = min(1.0, slope_sup_norm / 0.005)
        touch_conf = min(1.0, (len(res_idx) + len(sup_idx)) / 6)
        confidence = float(np.clip((res_conf + sup_conf + touch_conf) / 3, 0.0, 1.0))

        return {"pattern": "ascending_triangle", "confidence": confidence}

    except Exception as e:
        logger.error(f"[PATTERN] ascending_triangle error: {e}")
        return {"pattern": "none", "confidence": 0.0}


def _detect_head_and_shoulders(close, high, low, volume) -> dict:
    """
    Left shoulder < head > right shoulder, shoulders within 5% symmetry.
    """
    try:
        lookback  = min(40, len(close))
        h_sub     = high.iloc[-lookback:]
        l_sub     = low.iloc[-lookback:]
        window    = 5

        peaks       = (h_sub == h_sub.rolling(window=window, center=True).max()) & \
                      (h_sub.rolling(window=window, center=True).count() == window)
        peak_idx    = h_sub[peaks].index.tolist()
        peak_prices = h_sub.loc[peak_idx].tolist()

        if len(peak_idx) < 3:
            return {"pattern": "none", "confidence": 0.0}

        best_conf = 0.0
        for i in range(len(peak_idx) - 2):
            ls, hd, rs     = peak_prices[i], peak_prices[i+1], peak_prices[i+2]
            if hd <= ls or hd <= rs:
                continue
            sym_pct = abs(ls - rs) / hd
            if sym_pct > 0.05:
                continue
            shoulder_avg   = (ls + rs) / 2
            head_height    = (hd - shoulder_avg) / hd
            head_conf      = min(1.0, head_height / 0.03)
            sym_conf       = 1.0 - (sym_pct / 0.05)
            confidence     = float(np.clip((head_conf + sym_conf) / 2, 0.0, 1.0))
            best_conf      = max(best_conf, confidence)

        if best_conf < _CONFIDENCE_FLOOR:
            return {"pattern": "none", "confidence": 0.0}
        return {"pattern": "head_and_shoulders", "confidence": best_conf}

    except Exception as e:
        logger.error(f"[PATTERN] head_and_shoulders error: {e}")
        return {"pattern": "none", "confidence": 0.0}


# ---------------------------------------------------------------------------
# Fix 3: Momentum signal detectors (new)
# ---------------------------------------------------------------------------

def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(window=period, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(window=period, min_periods=1).mean()
    rs    = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def _detect_rsi_signal(close, high, low, volume) -> dict:
    """
    RSI < 30 → oversold → bullish.
    RSI > 70 → overbought → bearish.
    Confidence scales linearly from the threshold outward.
    """
    try:
        rsi     = _compute_rsi(close)
        rsi_val = float(rsi.iloc[-1])

        if rsi_val < 30:
            # Further below 30 = higher confidence (RSI 10 → 1.0, RSI 30 → 0.5)
            confidence = 0.5 + min(0.5, (30.0 - rsi_val) / 40.0)
            return {"pattern": "rsi_oversold",   "confidence": confidence, "direction": "bullish"}

        if rsi_val > 70:
            confidence = 0.5 + min(0.5, (rsi_val - 70.0) / 40.0)
            return {"pattern": "rsi_overbought", "confidence": confidence, "direction": "bearish"}

        return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}

    except Exception as e:
        logger.error(f"[PATTERN] RSI signal error: {e}")
        return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}


def _detect_macd_crossover(close, high, low, volume) -> dict:
    """
    MACD line crosses above signal line (bullish) or below (bearish).
    Checks the last 3 candles for a fresh crossover.
    Confidence is based on the magnitude of the cross relative to price.
    """
    try:
        if len(close) < 35:
            return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}

        ema12  = close.ewm(span=12, adjust=False).mean()
        ema26  = close.ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        diff   = macd - signal

        price_scale = float(close.iloc[-1]) * 0.001 + 1e-9

        for i in range(-4, -1):
            prev_diff = float(diff.iloc[i - 1])
            curr_diff = float(diff.iloc[i])

            if prev_diff < 0 and curr_diff > 0:
                confidence = 0.5 + min(0.5, abs(curr_diff) / price_scale / 10.0)
                return {
                    "pattern":    "macd_bullish_crossover",
                    "confidence": float(np.clip(confidence, 0.5, 1.0)),
                    "direction":  "bullish",
                }
            if prev_diff > 0 and curr_diff < 0:
                confidence = 0.5 + min(0.5, abs(curr_diff) / price_scale / 10.0)
                return {
                    "pattern":    "macd_bearish_crossover",
                    "confidence": float(np.clip(confidence, 0.5, 1.0)),
                    "direction":  "bearish",
                }

        return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}

    except Exception as e:
        logger.error(f"[PATTERN] MACD crossover error: {e}")
        return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}
