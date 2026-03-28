"""
Fix 10: Weight persistence and gradient-ascent learning from trade history.

Weights are stored in memory/weights.json.  On startup decision_agent loads
them, falling back to DEFAULT_WEIGHTS when no learned weights exist yet.

update_weights_from_trades() runs one pass of stochastic gradient ascent on
the binary cross-entropy loss over all completed (correct/wrong) trades whose
feature vectors were recorded at decision time.

Outcome encoding (maps to "did price go up?"):
    BUY  + correct → y = 1
    BUY  + wrong   → y = 0
    SELL + correct → y = 0
    SELL + wrong   → y = 1
"""

import json
import math
import os
from utils.logger import setup_logger

logger = setup_logger(__name__)

WEIGHTS_FILE = os.path.join(os.path.dirname(__file__), "weights.json")

# Canonical defaults — must stay in sync with decision_agent.DEFAULT_WEIGHTS
DEFAULT_WEIGHTS = {
    "w_bias":       0.1,
    "w_trend":      1.2,
    "w_sentiment":  0.8,
    "w_pattern":    1.5,
    "w_volatility": -0.5,
    "w_sr_signal":  1.0,
    "w_volume":     0.3,
}


def load_weights() -> dict:
    """Return persisted weights, or defaults if no file exists yet."""
    if not os.path.exists(WEIGHTS_FILE):
        return DEFAULT_WEIGHTS.copy()
    try:
        with open(WEIGHTS_FILE, "r") as f:
            saved = json.load(f)
        # Fill in any keys missing from old saves
        weights = DEFAULT_WEIGHTS.copy()
        weights.update(saved)
        return weights
    except Exception as e:
        logger.error(f"[WEIGHTS] Failed to load weights: {e}")
        return DEFAULT_WEIGHTS.copy()


def save_weights(weights: dict):
    """Persist current weights to disk."""
    try:
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
        logger.info(f"[WEIGHTS] Saved: {weights}")
    except Exception as e:
        logger.error(f"[WEIGHTS] Failed to save weights: {e}")


def _sigmoid(x: float) -> float:
    x = max(-500.0, min(500.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def update_weights_from_trades(trades: dict, learning_rate: float = 0.01) -> dict:
    """
    Gradient ascent on log-likelihood over completed trades.

    Each trade must have:
        action          : "BUY" | "SELL"
        result          : "correct" | "wrong"
        features_vector : dict (stored by action_agent at decision time)

    Returns updated weights dict (also persisted to disk).
    """
    weights = load_weights()

    eligible = [
        t for t in trades.values()
        if t.get("result") in ("correct", "wrong")
        and t.get("features_vector")
        and t.get("action") in ("BUY", "SELL")
    ]

    if not eligible:
        logger.warning("[WEIGHTS] No eligible trades found for weight update")
        return weights

    for trade in eligible:
        fv    = trade["features_vector"]
        action = trade["action"]
        result = trade["result"]

        # Ground truth: did price go up?
        y = 1.0 if (action == "BUY"  and result == "correct") or \
                   (action == "SELL" and result == "wrong")  else 0.0

        # Feature vector aligned to weight keys
        x = {
            "w_bias":       1.0,
            "w_trend":      fv.get("trend",              0.0),
            "w_sentiment":  fv.get("sentiment",          0.0),
            "w_pattern":    fv.get("pattern_direction",  0.0) * fv.get("pattern_confidence", 0.0),
            "w_volatility": fv.get("volatility_norm",    0.0),
            "w_sr_signal":  fv.get("sr_signal",          0.0),
            "w_volume":     fv.get("volume_signal",       0.0),
        }

        z = sum(weights.get(k, 0.0) * v for k, v in x.items())
        p = _sigmoid(z)
        error = y - p

        for k, xk in x.items():
            weights[k] = weights.get(k, 0.0) + learning_rate * error * xk

    save_weights(weights)
    logger.info(f"[WEIGHTS] Updated from {len(eligible)} trades (lr={learning_rate})")
    return weights
