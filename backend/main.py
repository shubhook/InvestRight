import os
import re
import traceback
from dotenv import load_dotenv
load_dotenv()  # Must run before any module that reads env vars at import time
from flask import Flask, request, jsonify
from agents.data_agent import fetch_and_package_data
from agents.analysis_agent import analyze_data
from utils.pattern_engine import detect_pattern
from agents.decision_agent import make_decision
from utils.risk_engine import apply_risk
from agents.action_agent import execute
from auth.jwt_handler import generate_token
from auth.middleware import require_auth
from safety.kill_switch import is_trading_halted, activate_kill_switch, deactivate_kill_switch
from utils.logger import setup_logger

logger = setup_logger(__name__)

_API_KEY = os.getenv("API_KEY")
if not _API_KEY:
    raise EnvironmentError("API_KEY environment variable is not set.")

_JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", 24))

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_response(symbol, decision, risk_adjusted, analysis, pattern):
    """Shared response structure for /analyze and run()."""
    return {
        "symbol":         symbol,
        "decision":       risk_adjusted.get("action", "WAIT"),
        "confidence":     decision.get("confidence",     0.0),
        "expected_value": decision.get("expected_value", 0.0),
        "probability_up": decision.get("probability_up", 0.5),
        "reason":         decision.get("reason",         ""),
        "risk": {
            "entry":                  risk_adjusted.get("entry"),
            "stop_loss":              risk_adjusted.get("stop_loss"),
            "target":                 risk_adjusted.get("target"),
            "rr_ratio":               risk_adjusted.get("rr_ratio"),
            "max_loss_pct":           risk_adjusted.get("max_loss_pct"),
            "position_size_fraction": risk_adjusted.get("position_size_fraction"),
            "decision_risk":          decision.get("risk", 0.0),
        },
        "analysis_summary": {
            "trend":            analysis.get("trend"),
            "support_count":    len(analysis.get("support", [])),
            "resistance_count": len(analysis.get("resistance", [])),
            "volatility":       analysis.get("volatility"),
            "sentiment":        analysis.get("sentiment"),
            "volume_signal":    analysis.get("volume_signal"),
        },
        "pattern_detected": {
            "pattern":    pattern.get("pattern"),
            "confidence": pattern.get("confidence"),
            "direction":  pattern.get("direction"),
        },
    }


# ---------------------------------------------------------------------------
# Public endpoints (no auth)
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """System status check — no auth required."""
    status = {"db": "unreachable", "redis": "unreachable", "kill_switch": "unknown"}
    overall = "ok"

    # DB check
    try:
        from db.connection import db_cursor
        import signal as _signal

        with db_cursor() as cur:
            cur.execute("SELECT 1")
        status["db"] = "connected"

        # Kill switch state (only if DB is up)
        halted = is_trading_halted()
        status["kill_switch"] = halted
    except Exception as e:
        logger.warning(f"[HEALTH] DB unreachable: {e}")
        overall = "degraded"

    # Redis check
    try:
        import redis as _redis
        r = _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=2)
        r.ping()
        status["redis"] = "connected"
    except Exception:
        overall = "degraded"

    status["status"] = overall
    return jsonify(status)


@app.route("/token", methods=["POST"])
def token():
    """Exchange API key for a JWT."""
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Bad Request", "message": "Request body required"}), 400

    api_key = body.get("api_key")
    if not api_key:
        return jsonify({"error": "Bad Request", "message": "api_key field required"}), 400

    if api_key != _API_KEY:
        return jsonify({"error": "Forbidden", "message": "Invalid API key"}), 403

    jwt_token = generate_token({"sub": "api_client", "role": "trader"})
    return jsonify({"token": jwt_token, "expires_in_hours": _JWT_EXPIRY_HOURS})


# ---------------------------------------------------------------------------
# Protected endpoints
# ---------------------------------------------------------------------------

@app.route("/analyze", methods=["GET"])
@require_auth
def analyze():
    """
    Main analysis endpoint.

    Query Parameters:
        symbol (str): Stock symbol to analyze (e.g., RELIANCE.NS)
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "Symbol parameter is required"}), 400

        if not re.match(r"^[A-Z0-9.\-]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        # Kill switch check — fail before running any pipeline logic
        if is_trading_halted():
            logger.warning(f"[API] Trading halted — analysis request blocked for {symbol}")
            return jsonify({
                "error": "Trading halted",
                "message": "Kill switch is active. Resume trading via POST /resume.",
            }), 503

        logger.info(f"[API] Analysis request for symbol: {symbol}")

        data = fetch_and_package_data(symbol)
        if data is None:
            logger.error(f"[API] Failed to fetch data for {symbol}")
            return jsonify({"error": "Failed to fetch data", "symbol": symbol}), 500

        analysis      = analyze_data(data)
        pattern       = detect_pattern(data["ohlc"])
        current_price = (
            float(data["ohlc"]["close"].iloc[-1])
            if data.get("ohlc") is not None and not data["ohlc"].empty
            else None
        )
        decision  = make_decision(analysis, pattern, current_price=current_price)
        risk_adj  = apply_risk(decision, analysis, data["ohlc"], symbol=symbol)
        execution = execute(risk_adj, symbol=symbol)

        response = _build_response(symbol, decision, risk_adj, analysis, pattern)
        response["execution"] = execution
        logger.info(f"[API] Analysis completed for {symbol}: {response['decision']}")
        return jsonify(response)

    except Exception as e:
        logger.error(f"[API] Error in analyze endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/update-weights", methods=["POST"])
@require_auth
def update_weights():
    """
    Trigger a gradient-ascent weight update from completed trades.

    Optional JSON body:
        { "learning_rate": 0.01 }
    """
    try:
        body          = request.get_json(silent=True) or {}
        learning_rate = float(body.get("learning_rate", 0.01))

        from memory.memory_store import get_all_trades
        from memory.weights_store import update_weights_from_trades

        trades      = get_all_trades()
        eligible    = [
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


@app.route("/halt", methods=["POST"])
@require_auth
def halt():
    """Activate the kill switch — immediately blocks all /analyze requests."""
    body         = request.get_json(silent=True) or {}
    reason       = body.get("reason", "no reason provided")
    activated_by = body.get("activated_by", "unknown")

    success = activate_kill_switch(reason, activated_by)
    if not success:
        return jsonify({"error": "Failed to activate kill switch"}), 500

    return jsonify({
        "status":  "halted",
        "message": "Kill switch activated",
        "reason":  reason,
    })


@app.route("/resume", methods=["POST"])
@require_auth
def resume():
    """Deactivate the kill switch — resumes normal trading."""
    success = deactivate_kill_switch()
    if not success:
        return jsonify({"error": "Failed to deactivate kill switch"}), 500

    return jsonify({
        "status":  "active",
        "message": "Kill switch deactivated. Trading resumed.",
    })


# ---------------------------------------------------------------------------
# Order endpoints
# ---------------------------------------------------------------------------

@app.route("/orders", methods=["GET"])
@require_auth
def list_orders():
    """Return all orders, most recent first."""
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT order_id, trade_id, symbol, action, quantity, status,
                       filled_price, filled_quantity, broker_mode,
                       placed_at, filled_at, failure_reason
                FROM orders
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        orders = []
        for row in rows:
            d = dict(zip(cols, row))
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif hasattr(v, "__float__"):
                    try: d[k] = float(v)
                    except Exception: pass
            if d.get("trade_id"):
                d["trade_id"] = str(d["trade_id"])
            orders.append(d)

        return jsonify({"orders": orders, "total": len(orders)})
    except Exception as e:
        logger.error(f"[API] /orders error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/orders/<order_id>", methods=["GET"])
@require_auth
def get_order(order_id):
    """Return a single order by order_id."""
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
            row = cur.fetchone()
            if row is None:
                return jsonify({"error": "Order not found"}), 404
            cols = [d[0] for d in cur.description]
            d = dict(zip(cols, row))

        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif hasattr(v, "__float__"):
                try: d[k] = float(v)
                except Exception: pass
        if d.get("trade_id"):
            d["trade_id"] = str(d["trade_id"])
        return jsonify(d)
    except Exception as e:
        logger.error(f"[API] /orders/{order_id} error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/orders/<order_id>/cancel", methods=["POST"])
@require_auth
def cancel_order(order_id):
    """Cancel an open order."""
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute(
                "SELECT status, broker_order_id, broker_mode FROM orders WHERE order_id = %s",
                (order_id,)
            )
            row = cur.fetchone()

        if row is None:
            return jsonify({"order_id": order_id, "cancelled": False,
                            "message": "Order not found"}), 404

        status, broker_order_id, broker_mode = row
        if status == "FILLED":
            return jsonify({"order_id": order_id, "cancelled": False,
                            "message": "Order already filled — cannot cancel"}), 400
        if status == "CANCELLED":
            return jsonify({"order_id": order_id, "cancelled": True,
                            "message": "Order already cancelled"})

        from broker.broker_factory import get_broker
        broker  = get_broker()
        success = broker.cancel_order(broker_order_id)

        if success:
            return jsonify({"order_id": order_id, "cancelled": True,
                            "message": "Order cancelled successfully"})
        return jsonify({"order_id": order_id, "cancelled": False,
                        "message": "Cancel request failed"}), 500

    except Exception as e:
        logger.error(f"[API] /orders/{order_id}/cancel error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/broker/status", methods=["GET"])
@require_auth
def broker_status():
    """Return current broker configuration and system state."""
    broker_mode   = os.getenv("BROKER_MODE", "paper").lower()
    total_capital = float(os.getenv("TOTAL_CAPITAL", 0))
    halted        = is_trading_halted()

    status = {
        "broker_mode":   broker_mode,
        "kill_switch":   halted,
        "total_capital": total_capital,
    }

    if broker_mode == "live":
        # Quick connectivity test
        try:
            from broker.kite_broker import _get_kite
            kite = _get_kite()
            kite.profile()
            status["kite_connected"] = True
        except Exception:
            status["kite_connected"] = False
    else:
        status["kite_connected"] = False
        status["paper_note"] = "Running in paper trading mode. No real orders placed."

    return jsonify(status)


# ---------------------------------------------------------------------------
# Portfolio endpoints
# ---------------------------------------------------------------------------

@app.route("/portfolio", methods=["GET"])
@require_auth
def portfolio():
    """Full portfolio summary — capital, P&L, positions, trade stats."""
    from portfolio.pnl_calculator import get_portfolio_summary
    return jsonify(get_portfolio_summary())


@app.route("/portfolio/positions", methods=["GET"])
@require_auth
def portfolio_positions():
    """All open positions with current P&L."""
    from portfolio.position_manager import get_open_positions
    positions = get_open_positions()
    total_unrealised = sum(
        float(p.get("unrealised_pnl") or 0) for p in positions
    )
    return jsonify({
        "positions":           positions,
        "total_open":          len(positions),
        "total_unrealised_pnl": round(total_unrealised, 2),
    })


@app.route("/portfolio/positions/<position_id>", methods=["GET"])
@require_auth
def portfolio_position(position_id):
    """Single position P&L detail."""
    from portfolio.pnl_calculator import get_position_pnl
    result = get_position_pnl(position_id)
    if result is None:
        return jsonify({"error": "Position not found"}), 404
    return jsonify(result)


@app.route("/portfolio/positions/<position_id>/close", methods=["POST"])
@require_auth
def close_position_manual(position_id):
    """
    Manually close a position.
    Kill switch does NOT block manual closes — exits must always work.
    """
    try:
        from portfolio.position_manager import get_position, close_position
        from broker.broker_factory import get_broker

        position = get_position(position_id)
        if position is None:
            return jsonify({"error": "Position not found"}), 404

        if position.get("status") == "closed":
            return jsonify({"error": "Position already closed"}), 400

        body        = request.get_json(silent=True) or {}
        exit_reason = body.get("reason", "manual")
        symbol      = position["symbol"]

        broker = get_broker()
        ltp    = broker.get_ltp(symbol)
        if ltp is None:
            ltp = float(position["current_price"] or position["entry_price"])
            logger.warning(f"[API] LTP unavailable for manual close of {symbol} — using last known price")

        closed = close_position(position_id, ltp, exit_reason)
        if closed is None:
            return jsonify({"error": "Failed to close position"}), 500

        # Record trade outcome
        from agents.feedback_agent import record_outcome
        if position.get("trade_id"):
            record_outcome(position["trade_id"], ltp, exit_reason)

        return jsonify(closed)

    except Exception as e:
        logger.error(f"[API] /portfolio/positions/{position_id}/close error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/portfolio/pnl", methods=["GET"])
@require_auth
def portfolio_pnl():
    """Full P&L breakdown."""
    from portfolio.pnl_calculator import get_portfolio_summary
    summary = get_portfolio_summary()
    return jsonify(summary["pnl"])


@app.route("/portfolio/pnl/daily", methods=["GET"])
@require_auth
def portfolio_pnl_daily():
    """Today's P&L summary."""
    from portfolio.pnl_calculator import get_daily_pnl
    return jsonify(get_daily_pnl())


# ---------------------------------------------------------------------------
# Programmatic pipeline (used by scheduler.py)
# ---------------------------------------------------------------------------

def run(symbol: str) -> dict:
    """
    Execute the full analysis pipeline for a given symbol.
    Used by the scheduler — does NOT go through auth middleware.
    """
    try:
        logger.info(f"[PIPELINE] Running pipeline for: {symbol}")

        if is_trading_halted():
            logger.warning(f"[PIPELINE] Trading halted — skipping pipeline for {symbol}")
            return {"error": "Trading halted", "symbol": symbol}

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
        risk_adj  = apply_risk(decision, analysis, data["ohlc"], symbol=symbol)
        execution = execute(risk_adj, symbol=symbol)
        result    = _build_response(symbol, decision, risk_adj, analysis, pattern)
        result["execution"] = execution

        logger.info(f"[PIPELINE] Completed for {symbol}: {result['decision']}")
        return result

    except Exception as e:
        logger.error(f"[PIPELINE] Error running pipeline for {symbol}: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5001)
