import os
import re
import uuid
import traceback
import threading
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
# Backtest endpoints
# ---------------------------------------------------------------------------

def _create_backtest_run_row(run_id, symbol, start_date, end_date, interval, initial_capital):
    """Insert a backtest_runs row so the run_id is valid before the thread starts."""
    from db.connection import db_cursor
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO backtest_runs
                (run_id, symbol, start_date, end_date, interval, initial_capital, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'running')
            """,
            (run_id, symbol, start_date, end_date, interval, initial_capital),
        )


def _backtest_thread(run_id, symbol, start_date, end_date, interval, initial_capital):
    """Background thread: load data then run the backtest engine."""
    from backtest.data_loader import load_historical_data
    from backtest.backtest_engine import run_backtest, _update_run_status

    ohlc_df = load_historical_data(symbol, start_date, end_date, interval=interval)
    if ohlc_df is None or ohlc_df.empty:
        _update_run_status(run_id, "failed", error="Failed to load historical data")
        return

    run_backtest(run_id=run_id, symbol=symbol, ohlc_df=ohlc_df, initial_capital=initial_capital)


@app.route("/backtest/run", methods=["POST"])
@require_auth
def backtest_run():
    """
    Launch an async backtest.  Returns run_id immediately; check status via
    GET /backtest/runs/<run_id>.

    Required JSON body:
        symbol          (str)   e.g. "RELIANCE.NS"
        start_date      (str)   "YYYY-MM-DD"
        end_date        (str)   "YYYY-MM-DD"
        interval        (str)   optional, default "1d"
        initial_capital (float) optional, default from BACKTEST_DEFAULT_CAPITAL
    """
    try:
        body = request.get_json(silent=True) or {}
        symbol = (body.get("symbol") or "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        if not re.match(r"^[A-Z0-9.\-]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        start_date = body.get("start_date", "")
        end_date   = body.get("end_date",   "")
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400

        interval        = body.get("interval", "1d")
        default_capital = float(os.getenv("BACKTEST_DEFAULT_CAPITAL", 100000.0))
        initial_capital = float(body.get("initial_capital", default_capital))

        run_id = str(uuid.uuid4())
        _create_backtest_run_row(run_id, symbol, start_date, end_date, interval, initial_capital)

        t = threading.Thread(
            target=_backtest_thread,
            args=(run_id, symbol, start_date, end_date, interval, initial_capital),
            daemon=True,
        )
        t.start()

        logger.info(f"[API] Backtest launched: run_id={run_id} symbol={symbol}")
        return jsonify({"run_id": run_id, "status": "running", "symbol": symbol}), 202

    except Exception as e:
        logger.error(f"[API] /backtest/run error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/backtest/runs", methods=["GET"])
@require_auth
def backtest_list_runs():
    """Return paginated list of all backtest runs."""
    from backtest.report_generator import list_runs
    limit  = min(int(request.args.get("limit",  50)), 200)
    offset = int(request.args.get("offset", 0))
    return jsonify(list_runs(limit=limit, offset=offset))


@app.route("/backtest/runs/<run_id>", methods=["GET"])
@require_auth
def backtest_get_run(run_id):
    """Return full summary for a single backtest run."""
    from backtest.report_generator import generate_summary
    summary = generate_summary(run_id)
    if summary is None:
        return jsonify({"error": "Backtest run not found"}), 404
    return jsonify(summary)


@app.route("/backtest/runs/<run_id>/trades", methods=["GET"])
@require_auth
def backtest_run_trades(run_id):
    """Return trade-by-trade breakdown for a run."""
    from backtest.report_generator import get_trade_breakdown
    return jsonify({"run_id": run_id, "trades": get_trade_breakdown(run_id)})


@app.route("/backtest/runs/<run_id>/equity-curve", methods=["GET"])
@require_auth
def backtest_equity_curve(run_id):
    """Return the equity curve for a run."""
    from backtest.report_generator import get_equity_curve
    return jsonify({"run_id": run_id, "equity_curve": get_equity_curve(run_id)})


@app.route("/backtest/compare", methods=["POST"])
@require_auth
def backtest_compare():
    """
    Compare multiple runs side-by-side.

    JSON body: { "run_ids": ["<uuid>", "<uuid>", ...] }
    """
    try:
        body    = request.get_json(silent=True) or {}
        run_ids = body.get("run_ids", [])
        if not run_ids or not isinstance(run_ids, list):
            return jsonify({"error": "run_ids list is required"}), 400

        from backtest.report_generator import generate_comparison
        return jsonify(generate_comparison(run_ids))

    except Exception as e:
        logger.error(f"[API] /backtest/compare error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/backtest/walk-forward", methods=["POST"])
@require_auth
def backtest_walk_forward():
    """
    Run walk-forward validation (synchronous — may take several minutes).

    Required JSON body:
        symbol          (str)
        start_date      (str)   "YYYY-MM-DD"
        end_date        (str)   "YYYY-MM-DD"
        interval        (str)   optional, default "1d"
        initial_capital (float) optional
        n_splits        (int)   optional, default 5
    """
    try:
        body = request.get_json(silent=True) or {}
        symbol = (body.get("symbol") or "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400

        start_date = body.get("start_date", "")
        end_date   = body.get("end_date",   "")
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400

        interval        = body.get("interval", "1d")
        default_capital = float(os.getenv("BACKTEST_DEFAULT_CAPITAL", 100000.0))
        initial_capital = float(body.get("initial_capital", default_capital))
        n_splits        = int(body.get("n_splits", 5))

        from backtest.data_loader import load_historical_data
        from backtest.walk_forward import run_walk_forward

        ohlc_df = load_historical_data(symbol, start_date, end_date, interval=interval)
        if ohlc_df is None or ohlc_df.empty:
            return jsonify({"error": "Failed to load historical data for symbol"}), 422

        result = run_walk_forward(
            symbol=symbol,
            ohlc_df=ohlc_df,
            initial_capital=initial_capital,
            n_splits=n_splits,
        )
        return jsonify(result)

    except Exception as e:
        logger.error(f"[API] /backtest/walk-forward error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


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
