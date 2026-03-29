import os
import re
import uuid
import traceback
import threading
from dotenv import load_dotenv
load_dotenv()  # Must run before any module that reads env vars at import time
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from observability.trace import TraceContext, generate_trace_id
from observability.audit_log import (
    log_event, log_pipeline_start, log_pipeline_end,
    DATA_FETCH, ANALYSIS_COMPLETE, PATTERN_DETECTED,
    DECISION_MADE, RISK_APPLIED, ORDER_PLACED,
)
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
from utils.rate_limiter import check_rate_limit

logger = setup_logger(__name__)

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


def _is_valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value)) if value else False


_API_KEY = os.getenv("API_KEY")
if not _API_KEY:
    raise EnvironmentError("API_KEY environment variable is not set.")

_JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", 24))

app = Flask(__name__)

# CORS — allow origins from CORS_ORIGINS env var (comma-separated)
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080")
CORS(app, origins=[o.strip() for o in _cors_origins.split(",") if o.strip()])


def _rate_limit_check(endpoint: str):
    """
    Apply rate limiting for the given endpoint.
    Returns a 429 Response if the limit is exceeded, else None.
    Client identity is the remote IP (or X-Forwarded-For).
    """
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    # Use first IP if there are multiple (proxy chain)
    client_id = client_ip.split(",")[0].strip()
    allowed, headers = check_rate_limit(client_id, endpoint)
    if not allowed:
        resp = jsonify({"error": "Too Many Requests",
                        "message": f"Rate limit exceeded for {endpoint}"})
        resp.status_code = 429
        for k, v in headers.items():
            resp.headers[k] = v
        return resp
    return None


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

    # Model health check
    try:
        from feedback.model_monitor import compute_accuracy_window
        mh = compute_accuracy_window(30)
        status["model_health"] = {
            "is_healthy":  mh["is_healthy"],
            "accuracy":    mh["accuracy"],
            "brier_score": mh["brier_score"],
            "sample_size": mh["completed_trades"],
        }
    except Exception:
        status["model_health"] = {"is_healthy": True, "accuracy": None,
                                  "brier_score": None, "sample_size": 0}

    # Kite token status — always included so UI shows connection state in any broker mode
    try:
        from auth.kite_token_refresh import is_token_valid, get_token_expiry
        expiry = get_token_expiry()
        status["kite_token"] = {
            "valid":       is_token_valid(),
            "valid_until": expiry.isoformat() if expiry else None,
        }
    except Exception:
        status["kite_token"] = {"valid": False, "valid_until": None}

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
    rl = _rate_limit_check("/analyze")
    if rl:
        return rl
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

        trace = TraceContext(generate_trace_id(), symbol)
        log_pipeline_start(trace)
        logger.info(f"[API] Analysis request for symbol: {symbol} trace={trace.trace_id}")

        data = fetch_and_package_data(symbol, trace=trace)
        if data is None:
            logger.error(f"[API] Failed to fetch data for {symbol}")
            log_event(trace.trace_id, DATA_FETCH, "data_agent",
                      f"Failed to fetch data for {symbol}", severity="ERROR", symbol=symbol)
            return jsonify({"error": "Failed to fetch data", "symbol": symbol}), 500

        log_event(trace.trace_id, DATA_FETCH, "data_agent",
                  f"Data fetched for {symbol}", symbol=symbol,
                  duration_ms=trace.elapsed_ms())

        analysis = analyze_data(data, trace=trace)
        log_event(trace.trace_id, ANALYSIS_COMPLETE, "analysis_agent",
                  f"Analysis complete: trend={analysis.get('trend')} "
                  f"sentiment={analysis.get('sentiment')}({analysis.get('sentiment_source')})",
                  symbol=symbol)

        pattern = detect_pattern(data["ohlc"], trace=trace)
        log_event(trace.trace_id, PATTERN_DETECTED, "pattern_engine",
                  f"Pattern: {pattern.get('pattern')} conf={pattern.get('confidence'):.2f}",
                  symbol=symbol)

        current_price = (
            float(data["ohlc"]["close"].iloc[-1])
            if data.get("ohlc") is not None and not data["ohlc"].empty
            else None
        )
        decision  = make_decision(analysis, pattern, current_price=current_price, trace=trace)
        log_event(trace.trace_id, DECISION_MADE, "decision_agent",
                  f"Decision: {decision.get('action')} p_up={decision.get('probability_up'):.3f}",
                  symbol=symbol)

        risk_adj  = apply_risk(decision, analysis, data["ohlc"], symbol=symbol, trace=trace)
        log_event(trace.trace_id, RISK_APPLIED, "risk_engine",
                  f"Risk: action={risk_adj.get('action')} entry={risk_adj.get('entry')}",
                  symbol=symbol)

        execution = execute(risk_adj, symbol=symbol, trace=trace)
        log_event(trace.trace_id, ORDER_PLACED, "action_agent",
                  f"Execution: executed={execution.get('executed')} "
                  f"reason={execution.get('reason')}",
                  symbol=symbol,
                  trade_id=execution.get("trade_id"))

        response = _build_response(symbol, decision, risk_adj, analysis, pattern)
        response["execution"] = execution
        response["trace_id"]  = trace.trace_id
        log_pipeline_end(trace, response["decision"])
        logger.info(f"[API] Analysis completed for {symbol}: {response['decision']}")
        return jsonify(response)

    except Exception as e:
        logger.error(f"[API] Error in analyze endpoint: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/sentiment", methods=["GET"])
@require_auth
def sentiment():
    """
    Read-only analysis scan of all active watchlist symbols.
    Runs data → analysis → pattern → decision pipeline only.
    Does NOT execute trades, does NOT check the kill switch.
    Returns bullish/bearish signals for each symbol.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime

    try:
        watchlist = _get_watchlist()
        active_symbols = [w["symbol"] for w in watchlist if w.get("is_active", True)]

        if not active_symbols:
            return jsonify({"results": [], "generated_at": datetime.utcnow().isoformat()})

        def _analyze_symbol(symbol):
            try:
                trace = TraceContext(generate_trace_id(), symbol)
                data = fetch_and_package_data(symbol, trace=trace)
                if data is None:
                    return {"symbol": symbol, "error": "Failed to fetch data"}
                analysis = analyze_data(data, trace=trace)
                pattern  = detect_pattern(data["ohlc"], trace=trace)
                current_price = (
                    float(data["ohlc"]["close"].iloc[-1])
                    if data.get("ohlc") is not None and not data["ohlc"].empty
                    else None
                )
                decision = make_decision(analysis, pattern, current_price=current_price, trace=trace)
                # Sentinel risk dict — no apply_risk or broker calls
                return _build_response(symbol, decision, {"action": decision.get("action", "WAIT")}, analysis, pattern)
            except Exception as e:
                logger.warning(f"[API] /sentiment failed for {symbol}: {e}")
                return {"symbol": symbol, "error": str(e)}

        results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_analyze_symbol, sym): sym for sym in active_symbols}
            for future in as_completed(futures):
                results.append(future.result())

        return jsonify({
            "results":      results,
            "generated_at": datetime.utcnow().isoformat(),
        })

    except Exception as e:
        logger.error(f"[API] /sentiment error: {e}")
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


@app.route("/broker/mode", methods=["POST"])
@require_auth
def set_broker_mode():
    """
    Switch broker mode at runtime — no restart required.
    JSON body: { "mode": "live" | "paper" }
    Switching to live is blocked if no valid Kite token exists.
    """
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").strip().lower()

    if mode not in ("paper", "live"):
        return jsonify({"error": "mode must be 'paper' or 'live'"}), 400

    if mode == "live":
        from auth.kite_token_refresh import is_token_valid
        if not is_token_valid():
            return jsonify({
                "error": "No valid Kite token. Connect your Zerodha account first (Settings tab)."
            }), 400

    os.environ["BROKER_MODE"] = mode
    logger.info(f"[API] Broker mode switched to: {mode}")
    return jsonify({"broker_mode": mode})


@app.route("/broker/kite/token", methods=["POST"])
@require_auth
def kite_store_token():
    """
    Store a new Kite access token.

    JSON body:
        access_token  (str, required)  — Kite access token from OAuth flow
        request_token (str, optional)  — Kite request token (for audit)
    """
    body = request.get_json(silent=True) or {}
    access_token  = (body.get("access_token") or "").strip()
    request_token = (body.get("request_token") or "").strip()

    if not access_token:
        return jsonify({"error": "access_token is required"}), 400

    from auth.kite_token_refresh import store_token, get_token_expiry
    ok = store_token(access_token, request_token)
    if not ok:
        return jsonify({"error": "Failed to store token"}), 500

    expiry = get_token_expiry()
    return jsonify({
        "stored":      True,
        "valid_until": expiry.isoformat() if expiry else None,
    })


# ---------------------------------------------------------------------------
# Kite OAuth endpoints (public — no auth required)
# ---------------------------------------------------------------------------

@app.route("/kite/login", methods=["GET"])
def kite_login_url():
    """
    Return the Zerodha login URL.
    The frontend redirects the user here to kick off OAuth.
    """
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        return jsonify({"error": "KITE_API_KEY not configured"}), 500
    url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
    return jsonify({"login_url": url})


@app.route("/kite/callback", methods=["GET"])
def kite_callback():
    """
    Zerodha redirects here after the user logs in.
    Exchanges the one-time request_token for a persistent access_token.

    Zerodha sends: ?request_token=XXX&status=success  (or status=error)
    """
    _frontend = os.getenv("FRONTEND_URL", "http://localhost:8080")

    status        = request.args.get("status", "")
    request_token = request.args.get("request_token", "").strip()

    if status != "success" or not request_token:
        logger.warning(f"[KITE_CALLBACK] Bad callback — status={status}")
        return redirect(f"{_frontend}/?kite=failed")

    try:
        from kiteconnect import KiteConnect
        api_key    = os.getenv("KITE_API_KEY")
        api_secret = os.getenv("KITE_API_SECRET")

        if not api_key or not api_secret:
            logger.error("[KITE_CALLBACK] KITE_API_KEY or KITE_API_SECRET not set")
            return redirect(f"{_frontend}/?kite=failed")

        kite = KiteConnect(api_key=api_key)
        session_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data["access_token"]

        from auth.kite_token_refresh import store_token
        store_token(access_token, request_token)

        logger.info("[KITE_CALLBACK] Access token stored — Kite connected successfully")
        return redirect(f"{_frontend}/?kite=connected")

    except Exception as e:
        logger.error(f"[KITE_CALLBACK] generate_session failed: {e}")
        return redirect(f"{_frontend}/?kite=failed")


# ---------------------------------------------------------------------------
# Watchlist endpoints
# ---------------------------------------------------------------------------

def _get_watchlist():
    from db.connection import db_cursor
    with db_cursor() as cur:
        cur.execute(
            "SELECT symbol, capital_pct, is_active, added_at, updated_at "
            "FROM watchlist ORDER BY added_at ASC"
        )
        cols = ["symbol", "capital_pct", "is_active", "added_at", "updated_at"]
        return [
            {k: (v.isoformat() if v is not None and hasattr(v, "isoformat")
                 else float(v) if k == "capital_pct" and v is not None
                 else v)
             for k, v in zip(cols, row)}
            for row in cur.fetchall()
        ]


@app.route("/watchlist", methods=["GET"])
@require_auth
def watchlist_get():
    """Return all watchlist entries."""
    try:
        return jsonify({"watchlist": _get_watchlist()})
    except Exception as e:
        logger.error(f"[API] /watchlist GET error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/watchlist", methods=["POST"])
@require_auth
def watchlist_add():
    """
    Add or update a symbol in the watchlist.

    JSON body:
        symbol      (str, required)  e.g. "RELIANCE.NS"
        capital_pct (float, optional) percentage of total capital, default 10
    """
    try:
        body = request.get_json(silent=True) or {}
        symbol = (body.get("symbol") or "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        if not re.match(r"^[A-Z0-9.\-]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        capital_pct = float(body.get("capital_pct", 10.0))
        if not (0 < capital_pct <= 100):
            return jsonify({"error": "capital_pct must be between 0 and 100"}), 400

        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO watchlist (symbol, capital_pct, is_active, updated_at)
                VALUES (%s, %s, TRUE, NOW())
                ON CONFLICT (symbol) DO UPDATE
                    SET capital_pct = EXCLUDED.capital_pct,
                        is_active   = TRUE,
                        updated_at  = NOW()
                """,
                (symbol, capital_pct),
            )
            # Sync capital limits table so risk engine picks up the allocation
            cur.execute(
                """
                INSERT INTO capital_limits (symbol, max_capital_pct, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (symbol) DO UPDATE
                    SET max_capital_pct = EXCLUDED.max_capital_pct,
                        updated_at      = NOW()
                """,
                (symbol, capital_pct),
            )

        logger.info(f"[API] Watchlist: added/updated {symbol} @ {capital_pct}%")
        return jsonify({"symbol": symbol, "capital_pct": capital_pct, "is_active": True}), 201

    except Exception as e:
        logger.error(f"[API] /watchlist POST error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/watchlist/<symbol>", methods=["DELETE"])
@require_auth
def watchlist_remove(symbol):
    """Remove a symbol from the watchlist."""
    try:
        symbol = symbol.strip().upper()
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("DELETE FROM watchlist WHERE symbol = %s", (symbol,))
            if cur.rowcount == 0:
                return jsonify({"error": "Symbol not in watchlist"}), 404
        logger.info(f"[API] Watchlist: removed {symbol}")
        return jsonify({"removed": symbol})
    except Exception as e:
        logger.error(f"[API] /watchlist DELETE error: {e}")
        return jsonify({"error": str(e)}), 500


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
    if not _is_valid_uuid(position_id):
        return jsonify({"error": "Invalid position_id format"}), 400
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
    if not _is_valid_uuid(position_id):
        return jsonify({"error": "Invalid position_id format"}), 400
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


@app.route("/portfolio/live", methods=["GET"])
@require_auth
def portfolio_live():
    """
    Fetch live holdings and intraday positions directly from Zerodha.
    Only works in live broker mode with a valid Kite token.
    """
    try:
        from broker.broker_factory import get_broker
        from broker.kite_broker import KiteBroker
        broker = get_broker()
        if not isinstance(broker, KiteBroker):
            return jsonify({
                "holdings":  [],
                "positions": [],
                "note":      "Live portfolio fetch only available in live broker mode.",
            })
        result = broker.get_portfolio()
        return jsonify(result)
    except Exception as e:
        logger.error(f"[API] /portfolio/live error: {e}")
        return jsonify({"error": str(e)}), 500


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


@app.route("/portfolio/summary", methods=["GET"])
@require_auth
def portfolio_summary_llm():
    """LLM-generated portfolio narrative summary."""
    try:
        from portfolio.pnl_calculator import get_portfolio_summary
        from memory.memory_store import get_all_trades
        from llm.summary_agent import generate_portfolio_summary

        timeframe     = request.args.get("timeframe", "7d")
        portfolio     = get_portfolio_summary()
        all_trades    = get_all_trades()
        trade_history = list(all_trades.values())

        summary = generate_portfolio_summary(
            portfolio_data=portfolio,
            trade_history=trade_history,
            timeframe=timeframe,
        )
        return jsonify({"summary": summary, "timeframe": timeframe})
    except Exception as e:
        logger.error(f"[API] /portfolio/summary error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/portfolio/daily-brief", methods=["GET"])
@require_auth
def portfolio_daily_brief():
    """LLM-generated daily trading brief."""
    try:
        from datetime import date
        from portfolio.pnl_calculator import get_daily_pnl
        from portfolio.position_manager import get_open_positions
        from llm.summary_agent import generate_daily_brief

        today     = str(date.today())
        daily_pnl = get_daily_pnl()
        positions = get_open_positions()
        brief     = generate_daily_brief(
            date=today,
            daily_pnl=daily_pnl,
            open_positions=positions,
        )
        return jsonify({"brief": brief, "date": today})
    except Exception as e:
        logger.error(f"[API] /portfolio/daily-brief error: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Observability endpoints
# ---------------------------------------------------------------------------

@app.route("/observability/trace/<trace_id>", methods=["GET"])
@require_auth
def observability_trace(trace_id):
    """Return the full event sequence for a pipeline trace."""
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT component, event_type, message, duration_ms,
                       severity, metadata, created_at
                FROM audit_log
                WHERE trace_id = %s::uuid
                ORDER BY created_at ASC
                """,
                (trace_id,),
            )
            rows = cur.fetchall()
            cols = ["component", "event_type", "message", "duration_ms",
                    "severity", "metadata", "created_at"]

        events = []
        total_ms = 0
        final_action = None
        for row in rows:
            d = dict(zip(cols, row))
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            if d.get("duration_ms"):
                total_ms += d["duration_ms"]
            if d.get("event_type") == "pipeline_end":
                meta = d.get("metadata") or {}
                final_action = meta.get("final_action")
            events.append(d)

        return jsonify({
            "trace_id":          trace_id,
            "events":            events,
            "total_duration_ms": total_ms,
            "final_action":      final_action,
        })
    except Exception as e:
        logger.error(f"[API] /observability/trace error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/observability/metrics", methods=["GET"])
@require_auth
def observability_metrics():
    """Return per-component latency and success-rate stats."""
    from observability.metrics import get_all_stats
    last_n = int(request.args.get("minutes", 60))
    stats  = get_all_stats(last_n_minutes=last_n)
    return jsonify({"components": stats, "period_minutes": last_n})


@app.route("/observability/audit", methods=["GET"])
@require_auth
def observability_audit():
    """
    Query the audit log.

    Query params: symbol, severity, event_type, since (ISO), limit (default 100, max 500)
    """
    try:
        from db.connection import db_cursor
        symbol     = request.args.get("symbol")
        severity   = request.args.get("severity")
        event_type = request.args.get("event_type")
        since      = request.args.get("since", "now() - interval '24 hours'")
        limit      = min(int(request.args.get("limit", 100)), 500)

        conditions = ["created_at >= %s"]
        params     = [since]

        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol)
        if severity:
            conditions.append("severity = %s")
            params.append(severity.upper())
        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type)

        params.append(limit)
        where = " AND ".join(conditions)

        with db_cursor() as cur:
            cur.execute(
                f"""
                SELECT log_id, trace_id, event_type, component, symbol,
                       severity, message, duration_ms, created_at
                FROM audit_log
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]

        events = []
        for row in rows:
            d = dict(zip(col_names, row))
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif hasattr(v, "__str__") and type(v).__name__ == "UUID":
                    d[k] = str(v)
            events.append(d)

        return jsonify({"events": events, "total": len(events), "limit": limit})
    except Exception as e:
        logger.error(f"[API] /observability/audit error: {e}")
        return jsonify({"error": str(e)}), 500


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
    rl = _rate_limit_check("/backtest/run")
    if rl:
        return rl
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
    if not _is_valid_uuid(run_id):
        return jsonify({"error": "Invalid run_id format"}), 400
    from backtest.report_generator import generate_summary
    summary = generate_summary(run_id)
    if summary is None:
        return jsonify({"error": "Backtest run not found"}), 404
    return jsonify(summary)


@app.route("/backtest/runs/<run_id>/trades", methods=["GET"])
@require_auth
def backtest_run_trades(run_id):
    """Return trade-by-trade breakdown for a run."""
    if not _is_valid_uuid(run_id):
        return jsonify({"error": "Invalid run_id format"}), 400
    from backtest.report_generator import get_trade_breakdown
    return jsonify({"run_id": run_id, "trades": get_trade_breakdown(run_id)})


@app.route("/backtest/runs/<run_id>/equity-curve", methods=["GET"])
@require_auth
def backtest_equity_curve(run_id):
    """Return the equity curve for a run."""
    if not _is_valid_uuid(run_id):
        return jsonify({"error": "Invalid run_id format"}), 400
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


def _walk_forward_thread(run_id, symbol, start_date, end_date, interval, initial_capital, n_splits):
    """Background thread: load data then run walk-forward validation."""
    from backtest.data_loader import load_historical_data
    from backtest.walk_forward import run_walk_forward
    from backtest.backtest_engine import _update_run_status
    import json

    ohlc_df = load_historical_data(symbol, start_date, end_date, interval=interval)
    if ohlc_df is None or ohlc_df.empty:
        _update_run_status(run_id, "failed", error="Failed to load historical data")
        return

    try:
        result = run_walk_forward(
            symbol=symbol,
            ohlc_df=ohlc_df,
            initial_capital=initial_capital,
            n_splits=n_splits,
        )
        _update_run_status(run_id, "completed", metrics=result)
    except Exception as e:
        _update_run_status(run_id, "failed", error=str(e))


@app.route("/backtest/walk-forward", methods=["POST"])
@require_auth
def backtest_walk_forward():
    """
    Launch an async walk-forward validation. Returns run_id immediately;
    check status via GET /backtest/runs/<run_id>.

    Required JSON body:
        symbol          (str)
        start_date      (str)   "YYYY-MM-DD"
        end_date        (str)   "YYYY-MM-DD"
        interval        (str)   optional, default "1d"
        initial_capital (float) optional
        n_splits        (int)   optional, default 5
    """
    rl = _rate_limit_check("/backtest/walk-forward")
    if rl:
        return rl
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
        n_splits        = int(body.get("n_splits", 5))

        run_id = str(uuid.uuid4())
        _create_backtest_run_row(run_id, symbol, start_date, end_date, interval, initial_capital)

        t = threading.Thread(
            target=_walk_forward_thread,
            args=(run_id, symbol, start_date, end_date, interval, initial_capital, n_splits),
            daemon=True,
        )
        t.start()

        logger.info(f"[API] Walk-forward launched: run_id={run_id} symbol={symbol}")
        return jsonify({"run_id": run_id, "status": "running"}), 202

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
        trace = TraceContext(generate_trace_id(), symbol)
        log_pipeline_start(trace)

        if is_trading_halted():
            logger.warning(f"[PIPELINE] Trading halted — skipping pipeline for {symbol}")
            return {"error": "Trading halted", "symbol": symbol, "trace_id": trace.trace_id}

        from utils.market_hours import is_market_open
        if not is_market_open():
            logger.info(f"[PIPELINE] Market closed — skipping pipeline for {symbol}")
            return {"decision": "WAIT", "reason": "Market closed", "symbol": symbol}

        data = fetch_and_package_data(symbol, trace=trace)
        if data is None:
            logger.error(f"[PIPELINE] Failed to fetch data for {symbol}")
            return {"error": f"Failed to fetch data for {symbol}", "trace_id": trace.trace_id}

        analysis      = analyze_data(data, trace=trace)
        pattern       = detect_pattern(data["ohlc"], trace=trace)
        current_price = (
            float(data["ohlc"]["close"].iloc[-1])
            if data.get("ohlc") is not None and not data["ohlc"].empty
            else None
        )
        decision  = make_decision(analysis, pattern, current_price=current_price, trace=trace)
        risk_adj  = apply_risk(decision, analysis, data["ohlc"], symbol=symbol, trace=trace)
        execution = execute(risk_adj, symbol=symbol, trace=trace)
        result    = _build_response(symbol, decision, risk_adj, analysis, pattern)
        result["execution"] = execution
        result["trace_id"]  = trace.trace_id
        log_pipeline_end(trace, result["decision"])

        logger.info(f"[PIPELINE] Completed for {symbol}: {result['decision']}")
        return result

    except Exception as e:
        logger.error(f"[PIPELINE] Error running pipeline for {symbol}: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}


if __name__ == "__main__":
    from config import validate_required_env
    validate_required_env()
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5001)
