import os
import re
import traceback
from flask import Flask, request, jsonify
from agents.data_agent import fetch_and_package_data
from agents.analysis_agent import analyze_data
from utils.pattern_engine import detect_pattern
from agents.decision_agent import make_decision
from utils.risk_engine import apply_risk
from agents.action_agent import execute
from utils.logger import setup_logger

logger = setup_logger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_response(symbol, decision, risk_adjusted, analysis, pattern):
    """Shared response structure for /analyze and run()."""
    return {
        "symbol":         symbol,
        "decision":       risk_adjusted.get("action", "WAIT"),
        "confidence":     decision.get("confidence",     0.0),   # Fix 6: |EV|/price
        "expected_value": decision.get("expected_value", 0.0),
        "probability_up": decision.get("probability_up", 0.5),
        "reason":         decision.get("reason",         ""),
        "risk": {
            "entry":                  risk_adjusted.get("entry"),
            "stop_loss":              risk_adjusted.get("stop_loss"),
            "target":                 risk_adjusted.get("target"),
            "rr_ratio":               risk_adjusted.get("rr_ratio"),
            "max_loss_pct":           risk_adjusted.get("max_loss_pct"),
            "position_size_fraction": risk_adjusted.get("position_size_fraction"),  # Fix 7
            "decision_risk":          decision.get("risk", 0.0),
        },
        "analysis_summary": {
            "trend":            analysis.get("trend"),
            "support_count":    len(analysis.get("support", [])),
            "resistance_count": len(analysis.get("resistance", [])),
            "volatility":       analysis.get("volatility"),
            "sentiment":        analysis.get("sentiment"),
            "volume_signal":    analysis.get("volume_signal"),   # Fix 9
        },
        "pattern_detected": {
            "pattern":    pattern.get("pattern"),
            "confidence": pattern.get("confidence"),
            "direction":  pattern.get("direction"),
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/analyze", methods=["GET"])
def analyze():
    """
    Main analysis endpoint.

    Query Parameters:
        symbol (str): Stock symbol to analyze (e.g., RELIANCE.NS)

    Returns:
        JSON: Analysis result with decision, reasoning, and risk parameters
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "Symbol parameter is required"}), 400

        if not re.match(r"^[A-Z0-9.\-]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        logger.info(f"[API] Analysis request for symbol: {symbol}")

        data = fetch_and_package_data(symbol)
        if data is None:
            logger.error(f"[API] Failed to fetch data for {symbol}")
            return jsonify({"error": "Failed to fetch data", "symbol": symbol}), 500

        analysis     = analyze_data(data)
        pattern      = detect_pattern(data["ohlc"])
        current_price = (
            float(data["ohlc"]["close"].iloc[-1])
            if data.get("ohlc") is not None and not data["ohlc"].empty
            else None
        )
        decision     = make_decision(analysis, pattern, current_price=current_price)
        risk_adj     = apply_risk(decision, analysis, data["ohlc"])

        response = _build_response(symbol, decision, risk_adj, analysis, pattern)
        logger.info(f"[API] Analysis completed for {symbol}: {response['decision']}")
        return jsonify(response)

    except Exception as e:
        logger.error(f"[API] Error in analyze endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/update-weights", methods=["POST"])
def update_weights():
    """
    Fix 10: Trigger a gradient-ascent weight update from completed trades.

    Optional JSON body:
        { "learning_rate": 0.01 }

    Returns:
        JSON: { "updated_weights": {...}, "trades_used": int }
    """
    try:
        body          = request.get_json(silent=True) or {}
        learning_rate = float(body.get("learning_rate", 0.01))

        from memory.memory_store import get_all_trades
        from memory.weights_store import update_weights_from_trades

        trades    = get_all_trades()
        eligible  = [
            t for t in trades.values()
            if t.get("result") in ("correct", "wrong") and t.get("features_vector")
        ]
        new_weights = update_weights_from_trades(trades, learning_rate=learning_rate)

        logger.info(f"[API] Weight update triggered: {len(eligible)} trades used")
        return jsonify({
            "updated_weights": new_weights,
            "trades_used":     len(eligible),
        })

    except Exception as e:
        logger.error(f"[API] Error in update-weights endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Weight update failed", "details": str(e)}), 500


# ---------------------------------------------------------------------------
# Programmatic pipeline (used by scheduler.py)
# ---------------------------------------------------------------------------

def run(symbol: str) -> dict:
    """
    Execute the full analysis pipeline for a given symbol.

    Args:
        symbol (str): Stock symbol (e.g., 'RELIANCE.NS')

    Returns:
        dict: Analysis result. Returns {"error": "..."} on failure.
    """
    try:
        logger.info(f"[PIPELINE] Running pipeline for: {symbol}")

        data = fetch_and_package_data(symbol)
        if data is None:
            logger.error(f"[PIPELINE] Failed to fetch data for {symbol}")
            return {"error": f"Failed to fetch data for {symbol}"}

        analysis      = analyze_data(data)
        pattern       = detect_pattern(data["ohlc"])
        current_price = (
            float(data["ohlc"]["close"].iloc[-1])
            if data.get("ohlc") is not None and not data["ohlc"].empty
            else None
        )
        decision  = make_decision(analysis, pattern, current_price=current_price)
        risk_adj  = apply_risk(decision, analysis, data["ohlc"])
        result    = _build_response(symbol, decision, risk_adj, analysis, pattern)

        logger.info(f"[PIPELINE] Completed for {symbol}: {result['decision']}")
        return result

    except Exception as e:
        logger.error(f"[PIPELINE] Error running pipeline for {symbol}: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5001)
