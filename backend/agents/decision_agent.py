"""
Probabilistic decision engine — all 10 fixes applied.

Fix 1  : Symmetric EV multipliers (gain = loss = 1.0) — removes structural BUY bias.
Fix 4  : Intercept / bias term in the logistic z-score (w_bias * 1.0).
Fix 5  : Continuous S/R signal via exponential decay + tanh — replaces binary ±1.
Fix 6  : confidence = |EV| / current_price — dimensionless, cross-symbol comparable.
Fix 8  : Volatility roles explicitly separated (vol_norm for logistic, raw ATR for EV).
Fix 9  : volume_signal added as a feature in encode_features and compute_probability.
Fix 10 : Weights loaded from weights.json at call time; features_vector included in
         output for action_agent to persist with the trade record.
"""

import math
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Fix 1 + Fix 4: symmetric multipliers, intercept weight added
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "w_bias":       0.1,    # Fix 4: intercept — encodes equity long-run drift prior
    "w_trend":      1.2,
    "w_sentiment":  0.8,
    "w_pattern":    1.5,
    "w_volatility": -0.5,   # Fix 8: applied to vol_norm (ATR/price), not raw ATR
    "w_sr_signal":  1.0,
    "w_volume":     0.3,    # Fix 9
}

DEFAULT_EV_CONFIG = {
    # Fix 1: symmetric gain/loss multipliers → break-even at P(up) = 0.50
    "gain_multiplier":  1.0,
    "loss_multiplier":  1.0,
    "ev_threshold":     0.005,  # dimensionless (EV/price); ~0.5% move threshold
    # Fix 5: exponential decay sigma for S/R proximity (fraction of price)
    "sr_decay_sigma":   0.03,
}


# ---------------------------------------------------------------------------
# Step 1 — Feature encoding (Fix 8: separate vol_norm; Fix 9: volume_signal)
# ---------------------------------------------------------------------------
def encode_features(
    analysis: dict,
    pattern: dict,
    current_price: float = None,
) -> dict:
    """
    Convert all categorical / raw inputs into numeric model features.

    Returns a dict with:
        trend              : +1 / 0 / −1
        sentiment          : +1 / 0 / −1
        pattern_direction  : +1 / 0 / −1
        pattern_confidence : float [0, 1]
        volatility         : raw ATR (price units) — used for EV gain/loss sizing
        volatility_norm    : ATR / price — used in logistic z-score (Fix 8)
        volume_signal      : Fix 9 — clipped ±2 relative volume
    """
    trend_raw = analysis.get("trend", "")
    trend     = 1.0 if trend_raw == "uptrend" else (-1.0 if trend_raw == "downtrend" else 0.0)

    sentiment_raw = analysis.get("sentiment", "neutral")
    sentiment     = (1.0  if sentiment_raw == "positive" else
                     -1.0 if sentiment_raw == "negative" else 0.0)

    direction_raw     = pattern.get("direction", "neutral")
    pattern_direction = (1.0  if direction_raw == "bullish" else
                         -1.0 if direction_raw == "bearish" else 0.0)

    pattern_confidence = float(pattern.get("confidence", 0.0))
    volatility         = float(analysis.get("volatility", 0.0))

    # Fix 8: normalise by price so the weight is scale-invariant across stocks
    if current_price and current_price > 0:
        volatility_norm = volatility / current_price
    else:
        volatility_norm = volatility

    # Fix 9: volume_signal already computed by analysis_agent
    volume_signal = float(analysis.get("volume_signal", 0.0))

    return {
        "trend":              trend,
        "sentiment":          sentiment,
        "pattern_direction":  pattern_direction,
        "pattern_confidence": pattern_confidence,
        "volatility":         volatility,
        "volatility_norm":    volatility_norm,
        "volume_signal":      volume_signal,
    }


# ---------------------------------------------------------------------------
# Fix 5 — Continuous support / resistance signal (exponential decay + tanh)
# ---------------------------------------------------------------------------
def compute_support_resistance_signal(
    analysis: dict,
    current_price: float = None,
    config: dict = None,
) -> float:
    """
    Returns a continuous value in (−1, +1):
        strongly positive → near support (bullish)
        strongly negative → near resistance (bearish)
        near zero         → not close to either

    Each level contributes exp(−distance / (price × sigma)).
    The net signal is passed through tanh to stay bounded.
    """
    if config is None:
        config = DEFAULT_EV_CONFIG
    if current_price is None or current_price <= 0:
        return 0.0

    sigma = config.get("sr_decay_sigma", 0.03)

    support_pull = sum(
        math.exp(-abs(current_price - s) / (current_price * sigma))
        for s in analysis.get("support", [])
        if s > 0 and s <= current_price
    )
    resistance_push = sum(
        math.exp(-abs(current_price - r) / (current_price * sigma))
        for r in analysis.get("resistance", [])
        if r > 0 and r >= current_price
    )

    return math.tanh(support_pull - resistance_push)


# ---------------------------------------------------------------------------
# Step 2 — Probability model  P(up | features)
# ---------------------------------------------------------------------------
def compute_probability(
    features: dict,
    sr_signal: float,
    weights: dict = None,
) -> float:
    """
    Logistic regression-style model.

    P(up) = sigmoid(
        w_bias      * 1              +   Fix 4
        w_trend     * trend          +
        w_sentiment * sentiment      +
        w_pattern   * dir * conf     +
        w_volatility* vol_norm       +   Fix 8
        w_sr_signal * sr_signal      +   Fix 5 (continuous)
        w_volume    * volume_signal      Fix 9
    )
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    z = (
        weights.get("w_bias",       0.0) * 1.0 +
        weights.get("w_trend",      0.0) * features["trend"] +
        weights.get("w_sentiment",  0.0) * features["sentiment"] +
        weights.get("w_pattern",    0.0) * features["pattern_direction"] * features["pattern_confidence"] +
        weights.get("w_volatility", 0.0) * features["volatility_norm"] +
        weights.get("w_sr_signal",  0.0) * sr_signal +
        weights.get("w_volume",     0.0) * features["volume_signal"]
    )

    return 1.0 / (1.0 + math.exp(-z))


# ---------------------------------------------------------------------------
# Step 4 — Expected value (Fix 1: symmetric multipliers)
# ---------------------------------------------------------------------------
def compute_expected_value(
    p_up: float,
    volatility: float,
    config: dict = None,
) -> tuple:
    """
    Returns (ev_raw, ev_normalised).

    ev_raw        = P(win) × AvgGain − P(loss) × AvgLoss    [price units]
    ev_normalised = ev_raw / current_price                   [Fix 6: dimensionless]

    AvgGain = volatility × gain_multiplier
    AvgLoss = volatility × loss_multiplier
    Fix 1: gain_multiplier = loss_multiplier = 1.0 → break-even at P(up) = 0.50
    """
    if config is None:
        config = DEFAULT_EV_CONFIG
    p_loss   = 1.0 - p_up
    avg_gain = volatility * config["gain_multiplier"]
    avg_loss = volatility * config["loss_multiplier"]
    ev_raw   = p_up * avg_gain - p_loss * avg_loss
    return ev_raw


# ---------------------------------------------------------------------------
# Step 4b — Risk proxy
# ---------------------------------------------------------------------------
def compute_risk(volatility: float, p_loss: float) -> float:
    return volatility * p_loss


# ---------------------------------------------------------------------------
# Fix 10 — Weight loading (lazy import to avoid circular deps at module level)
# ---------------------------------------------------------------------------
def _load_weights() -> dict:
    try:
        from memory.weights_store import load_weights
        return load_weights()
    except Exception:
        return DEFAULT_WEIGHTS.copy()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def make_decision(
    analysis: dict,
    pattern: dict,
    current_price: float = None,
    weights: dict = None,
    config: dict = None,
) -> dict:
    """
    Probabilistic trading decision engine.

    Args:
        analysis      : dict — trend, support, resistance, volatility,
                                sentiment, volume_signal
        pattern       : dict — pattern name, confidence, direction
        current_price : float | None — latest close (for S/R proximity + normalisation)
        weights       : dict | None — override weights (else loaded from weights.json)
        config        : dict | None — EV/threshold config

    Returns:
        {
            "action"         : "BUY" | "SELL" | "WAIT",
            "confidence"     : float,  Fix 6 — |EV| / price (dimensionless)
            "expected_value" : float,  raw EV in price units
            "probability_up" : float,  P(up) ∈ (0, 1)
            "risk"           : float,  volatility × P(loss)
            "reason"         : str,
            "features_vector": dict,   Fix 10 — stored with trade for weight learning
        }
    """
    try:
        # Fix 10: use learned weights when no override provided
        if weights is None:
            weights = _load_weights()
        if config is None:
            config = DEFAULT_EV_CONFIG

        volatility = float(analysis.get("volatility", 0.0))

        # Step 1 — encode
        features  = encode_features(analysis, pattern, current_price)

        # Fix 5 — continuous S/R signal
        sr_signal = compute_support_resistance_signal(analysis, current_price, config)

        # Step 2 — probability
        p_up   = compute_probability(features, sr_signal, weights)
        p_loss = 1.0 - p_up

        # Step 4 — EV
        ev_raw = compute_expected_value(p_up, volatility, config)

        # Fix 6 — normalise EV by price for comparable confidence
        if current_price and current_price > 0:
            ev_norm = ev_raw / current_price
        else:
            ev_norm = ev_raw

        # Step 4b — risk proxy
        risk = compute_risk(volatility, p_loss)

        # Step 5 — decision rule on normalised EV
        threshold = config.get("ev_threshold", 0.005)
        if ev_norm > threshold:
            action = "BUY"
        elif ev_norm < -threshold:
            action = "SELL"
        else:
            action = "WAIT"

        # Fix 6 — confidence = |EV_norm|  (dimensionless %, comparable across symbols)
        confidence = abs(ev_norm)

        reason = (
            f"P(up)={p_up:.3f}, EV={ev_raw:.4f} (norm={ev_norm:.5f}, threshold ±{threshold}), "
            f"trend={features['trend']:+.0f}, sentiment={features['sentiment']:+.0f}, "
            f"pattern={pattern.get('pattern','none')} "
            f"(dir={features['pattern_direction']:+.0f} × conf={features['pattern_confidence']:.2f}), "
            f"S/R={sr_signal:.3f}, vol_norm={features['volatility_norm']:.4f}, "
            f"volume_signal={features['volume_signal']:.3f}, "
            f"risk(vol×P_loss)={risk:.4f}"
        )

        logger.info(f"[DECISION] {action}: {reason}")

        # Fix 10: expose features for persistence alongside the trade record
        features_vector = {
            "trend":              features["trend"],
            "sentiment":          features["sentiment"],
            "pattern_direction":  features["pattern_direction"],
            "pattern_confidence": features["pattern_confidence"],
            "volatility_norm":    features["volatility_norm"],
            "sr_signal":          sr_signal,
            "volume_signal":      features["volume_signal"],
        }

        return {
            "action":          action,
            "confidence":      confidence,
            "expected_value":  ev_raw,
            "probability_up":  p_up,
            "risk":            risk,
            "reason":          reason,
            "features_vector": features_vector,
        }

    except Exception as e:
        logger.error(f"[DECISION] Error in probabilistic decision engine: {str(e)}")
        return {
            "action":          "WAIT",
            "confidence":      0.0,
            "expected_value":  0.0,
            "probability_up":  0.5,
            "risk":            0.0,
            "reason":          f"Decision engine error: {str(e)}",
            "features_vector": {},
        }
