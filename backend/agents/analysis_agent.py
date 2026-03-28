import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Fix 2: Expanded, weighted sentiment keyword lists.
# Keys are keywords; values are weights (higher = stronger signal).
# Lower normalised threshold (0.05 vs old 0.1) so headlines that contain even
# a few clear words are captured.
# ---------------------------------------------------------------------------
_POSITIVE_WORDS = {
    "growth": 1.0, "profit": 1.0, "upgrade": 1.5, "beat": 1.0,
    "surge": 1.5, "earnings": 1.0, "outperform": 1.5, "revenue": 0.5,
    "expansion": 1.0, "dividend": 0.5, "acquisition": 0.5, "record": 1.0,
    "strong": 1.0, "recovery": 1.0, "rally": 1.5, "buyback": 1.0,
    "optimistic": 1.0, "gain": 1.0, "raised": 1.0, "bullish": 1.5,
}

_NEGATIVE_WORDS = {
    "loss": 1.0, "fraud": 2.0, "downgrade": 1.5, "miss": 1.0,
    "crash": 2.0, "decline": 1.0, "fall": 1.0, "underperform": 1.5,
    "lawsuit": 1.5, "bankruptcy": 2.0, "debt": 0.5, "warning": 1.0,
    "weak": 1.0, "cut": 1.0, "layoff": 1.5, "penalty": 1.0,
    "probe": 1.0, "investigation": 1.5, "default": 2.0, "bearish": 1.5,
}

_SENTIMENT_THRESHOLD = 0.05   # was 0.1 — easier for real headlines to cross


def analyze_data(data: dict) -> dict:
    """
    Extract structured signals from raw data.

    Args:
        data (dict): Output from data_agent containing:
            - symbol   : str
            - ohlc     : pd.DataFrame [open, high, low, close, volume]
            - volume   : pd.Series  (may also be inside ohlc)
            - news     : List[str]

    Returns:
        dict:
            - trend          : "uptrend" | "downtrend"
            - support        : List[float]
            - resistance     : List[float]
            - volatility     : float  (ATR in price units)
            - sentiment      : "positive" | "negative" | "neutral"
            - volume_signal  : float  (Fix 9: current vol vs 20-period avg, clamped ±2)
    """
    try:
        ohlc   = data.get("ohlc")
        volume = data.get("volume")
        news   = data.get("news", [])

        if ohlc is None or ohlc.empty:
            logger.error("[ANALYSIS] No OHLC data provided")
            return _safe_analysis_default()

        required_cols = ["open", "high", "low", "close"]
        if not all(col in ohlc.columns for col in required_cols):
            logger.error("[ANALYSIS] Missing required OHLC columns")
            return _safe_analysis_default()

        close = ohlc["close"]
        high  = ohlc["high"]
        low   = ohlc["low"]

        # ------------------------------------------------------------------
        # 1. Trend — SMA(20) vs SMA(50)
        # ------------------------------------------------------------------
        sma_20 = close.rolling(window=20, min_periods=1).mean()
        sma_50 = close.rolling(window=50, min_periods=1).mean()
        trend  = "uptrend" if sma_20.iloc[-1] > sma_50.iloc[-1] else "downtrend"

        # ------------------------------------------------------------------
        # 2. Support / Resistance — rolling local minima / maxima
        # ------------------------------------------------------------------
        window = 10
        support_mask = (
            (low == low.rolling(window=window, center=True).min()) &
            (low.rolling(window=window, center=True).count() == window)
        )
        support_levels = sorted(
            [float(x) for x in low[support_mask].unique() if not pd.isna(x)]
        )[-5:]

        resistance_mask = (
            (high == high.rolling(window=window, center=True).max()) &
            (high.rolling(window=window, center=True).count() == window)
        )
        resistance_levels = sorted(
            [float(x) for x in high[resistance_mask].unique() if not pd.isna(x)],
            reverse=True,
        )[:5]

        # ------------------------------------------------------------------
        # 3. Volatility — 14-period ATR
        # ------------------------------------------------------------------
        prev_close = close.shift(1)
        true_range = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr        = true_range.rolling(window=14).mean()
        volatility = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else float(close.std())

        # ------------------------------------------------------------------
        # 4. Sentiment — Fix 2: weighted keywords, lower threshold
        # ------------------------------------------------------------------
        sentiment = _compute_sentiment(news)

        # ------------------------------------------------------------------
        # 5. Volume signal — Fix 9: (current_vol − avg_vol_20) / avg_vol_20
        # ------------------------------------------------------------------
        vol_series = volume if volume is not None else ohlc.get("volume")
        if vol_series is None and "volume" in ohlc.columns:
            vol_series = ohlc["volume"]

        volume_signal = _compute_volume_signal(vol_series)

        result = {
            "trend":         trend,
            "support":       support_levels,
            "resistance":    resistance_levels,
            "volatility":    volatility,
            "sentiment":     sentiment,
            "volume_signal": volume_signal,
        }

        logger.info(
            f"[ANALYSIS] trend={trend}, vol={volatility:.4f}, "
            f"sentiment={sentiment}, volume_signal={volume_signal:.3f}, "
            f"support={len(support_levels)}, resistance={len(resistance_levels)}"
        )
        return result

    except Exception as e:
        logger.error(f"[ANALYSIS] Error in analysis: {str(e)}")
        return _safe_analysis_default()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_sentiment(news: list) -> str:
    """
    Fix 2: weighted keyword scoring, normalised per headline, threshold 0.05.
    """
    if not news:
        return "neutral"

    total_score = 0.0
    for headline in news:
        h = headline.lower()
        pos = sum(w for kw, w in _POSITIVE_WORDS.items() if kw in h)
        neg = sum(w for kw, w in _NEGATIVE_WORDS.items() if kw in h)
        total_score += (pos - neg)

    normalised = total_score / len(news)

    if normalised > _SENTIMENT_THRESHOLD:
        return "positive"
    if normalised < -_SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


def _compute_volume_signal(vol_series) -> float:
    """
    Fix 9: returns (current_volume − avg_volume_20) / avg_volume_20,
    clamped to [−2, +2].
    Positive = above-average volume (conviction/breakout).
    Negative = below-average volume (lack of participation).
    """
    if vol_series is None or len(vol_series) == 0:
        return 0.0
    try:
        avg_vol     = float(vol_series.rolling(window=20, min_periods=1).mean().iloc[-1])
        current_vol = float(vol_series.iloc[-1])
        if avg_vol <= 0:
            return 0.0
        signal = (current_vol - avg_vol) / avg_vol
        return float(np.clip(signal, -2.0, 2.0))
    except Exception:
        return 0.0


def _safe_analysis_default() -> dict:
    return {
        "trend":         "downtrend",
        "support":       [],
        "resistance":    [],
        "volatility":    0.0,
        "sentiment":     "neutral",
        "volume_signal": 0.0,
    }
