"""
Backtesting engine — reuses the EXACT same pipeline components as live trading.

Isolation guarantees:
  - Writes ONLY to backtest_runs / backtest_trades / backtest_equity_curve
  - Never touches: trades, positions, capital_account tables
  - Never calls broker.place_order / deploy_capital / release_capital
  - Passes symbol=None to apply_risk → skips capital-limit DB checks
  - Exit checks use bar HIGH/LOW (not just close) for realistic simulation
"""
import json
from typing import Optional

import pandas as pd

from agents.analysis_agent import analyze_data
from agents.decision_agent import make_decision
from utils.pattern_engine import detect_pattern
from utils.risk_engine import apply_risk
from backtest.performance import compute_metrics
from db.connection import db_cursor
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Minimum bars of look-back required before issuing the first signal
MIN_HISTORY_BARS = 60


def run_backtest(
    run_id: str,
    symbol: str,
    ohlc_df: pd.DataFrame,
    initial_capital: float,
    weights: Optional[dict] = None,
) -> dict:
    """
    Run a full backtest over a historical OHLCV DataFrame.

    Args:
        run_id:          UUID string that already exists in backtest_runs.
        symbol:          Stock symbol (e.g. "RELIANCE.NS").
        ohlc_df:         Historical OHLCV DataFrame indexed by datetime.
        initial_capital: Starting capital for P&L simulation.
        weights:         Optional model weight override (default: live weights).

    Returns:
        dict with keys: run_id, metrics, trades, equity_curve  (or 'error').
    """
    try:
        _update_run_status(run_id, "running")

        capital      = float(initial_capital)
        position     = None   # open simulated position or None
        trades       = []
        equity_curve = [{"bar_index": 0, "bar_time": None, "equity": capital}]
        bar_count    = len(ohlc_df)

        for i in range(MIN_HISTORY_BARS, bar_count):
            bar      = ohlc_df.iloc[i]
            bar_time = ohlc_df.index[i]
            bar_high  = float(bar["high"])
            bar_low   = float(bar["low"])
            bar_close = float(bar["close"])

            # ------------------------------------------------------------------
            # Exit check BEFORE new entry — uses bar high/low
            # ------------------------------------------------------------------
            if position is not None:
                exit_reason = _check_exit(position, bar_high, bar_low)
                if exit_reason:
                    exit_price = _exit_price(position, exit_reason)
                    pnl        = _compute_pnl(position, exit_price)
                    capital   += pnl

                    trade = {
                        "run_id":         run_id,
                        "bar_index":      i,
                        "symbol":         symbol,
                        "action":         position["action"],
                        "entry_price":    position["entry"],
                        "exit_price":     exit_price,
                        "stop_loss":      position["stop_loss"],
                        "target":         position["target"],
                        "quantity":       position["quantity"],
                        "pnl":            round(pnl, 2),
                        "exit_reason":    exit_reason,
                        "result":         "correct" if exit_reason == "target_hit" else "wrong",
                        "entry_bar_time": position["bar_time"],
                        "exit_bar_time":  bar_time,
                    }
                    trades.append(trade)
                    _insert_backtest_trade(trade)
                    position = None

            # ------------------------------------------------------------------
            # No new entries while in a position (one trade at a time)
            # ------------------------------------------------------------------
            if position is not None:
                equity_curve.append({"bar_index": i, "bar_time": bar_time, "equity": round(capital, 2)})
                continue

            # ------------------------------------------------------------------
            # Analysis pipeline — same components as live, read-only slice
            # ------------------------------------------------------------------
            slice_df = ohlc_df.iloc[max(0, i - 200): i + 1].copy()

            data = {
                "symbol": symbol,
                "ohlc":   slice_df,
                "volume": slice_df["volume"] if "volume" in slice_df.columns else None,
                "news":   [],   # no live news feed in backtest
            }

            analysis = analyze_data(data)
            pattern  = detect_pattern(data["ohlc"])
            decision = make_decision(
                analysis,
                pattern,
                current_price=bar_close,
                weights=weights,
            )

            # symbol=None → apply_risk skips capital-limit DB checks
            risk_adj = apply_risk(decision, analysis, slice_df, symbol=None)

            if risk_adj.get("action") in ("BUY", "SELL"):
                quantity = _calculate_quantity(
                    risk_adj.get("position_size_fraction", 0.0),
                    risk_adj.get("entry") or bar_close,
                    capital,
                )
                if quantity > 0:
                    position = {
                        "action":    risk_adj["action"],
                        "entry":     risk_adj["entry"] or bar_close,
                        "stop_loss": risk_adj["stop_loss"],
                        "target":    risk_adj["target"],
                        "quantity":  quantity,
                        "bar_time":  bar_time,
                    }

            equity_curve.append({"bar_index": i, "bar_time": bar_time, "equity": round(capital, 2)})

        # ------------------------------------------------------------------
        # Force-close any open position at the last bar's close price
        # ------------------------------------------------------------------
        if position is not None:
            last_close = float(ohlc_df.iloc[-1]["close"])
            last_time  = ohlc_df.index[-1]
            pnl        = _compute_pnl(position, last_close)
            capital   += pnl

            trade = {
                "run_id":         run_id,
                "bar_index":      bar_count - 1,
                "symbol":         symbol,
                "action":         position["action"],
                "entry_price":    position["entry"],
                "exit_price":     last_close,
                "stop_loss":      position["stop_loss"],
                "target":         position["target"],
                "quantity":       position["quantity"],
                "pnl":            round(pnl, 2),
                "exit_reason":    "end_of_data",
                "result":         "pending",
                "entry_bar_time": position["bar_time"],
                "exit_bar_time":  last_time,
            }
            trades.append(trade)
            _insert_backtest_trade(trade)

        # ------------------------------------------------------------------
        # Metrics + persist
        # ------------------------------------------------------------------
        metrics = compute_metrics(trades, initial_capital)
        _insert_equity_curve(run_id, equity_curve)
        _update_run_status(run_id, "completed", metrics=metrics)

        logger.info(
            f"[BACKTEST] run_id={run_id} completed — "
            f"trades={metrics['total_trades']}, "
            f"return={metrics['total_return_pct']}%, "
            f"sharpe={metrics['sharpe_ratio']}"
        )

        return {
            "run_id":       run_id,
            "metrics":      metrics,
            "trades":       trades,
            "equity_curve": equity_curve,
        }

    except Exception as e:
        logger.error(f"[BACKTEST] run_id={run_id} failed: {e}")
        _update_run_status(run_id, "failed", error=str(e))
        return {"run_id": run_id, "error": str(e)}


# ---------------------------------------------------------------------------
# Exit logic — bar high/low (per spec)
# ---------------------------------------------------------------------------

def _check_exit(position: dict, bar_high: float, bar_low: float) -> Optional[str]:
    """Return exit reason string or None if no exit is triggered."""
    action    = position["action"]
    stop_loss = float(position["stop_loss"])
    target    = float(position["target"])

    if action == "BUY":
        if bar_high >= target:
            return "target_hit"
        if bar_low <= stop_loss:
            return "stop_hit"
    elif action == "SELL":
        if bar_low <= target:
            return "target_hit"
        if bar_high >= stop_loss:
            return "stop_hit"
    return None


def _exit_price(position: dict, exit_reason: str) -> float:
    """Return the exact price at which exit triggered (SL or target level)."""
    if exit_reason == "target_hit":
        return float(position["target"])
    if exit_reason == "stop_hit":
        return float(position["stop_loss"])
    return float(position["entry"])


def _compute_pnl(position: dict, exit_price: float) -> float:
    qty   = int(position["quantity"])
    entry = float(position["entry"])
    if position["action"] == "BUY":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def _calculate_quantity(
    position_size_fraction: float,
    entry_price: float,
    capital: float,
) -> int:
    """Floor-divide capital × fraction by entry price to get whole shares."""
    if entry_price <= 0 or capital <= 0 or position_size_fraction <= 0:
        return 0
    fraction = min(float(position_size_fraction), 1.0)
    return int((capital * fraction) // entry_price)


# ---------------------------------------------------------------------------
# DB helpers — backtest_* tables ONLY
# ---------------------------------------------------------------------------

def _update_run_status(
    run_id: str,
    status: str,
    metrics: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    try:
        with db_cursor() as cur:
            if status == "completed":
                cur.execute(
                    """
                    UPDATE backtest_runs
                    SET status = %s, metrics = %s, completed_at = NOW()
                    WHERE run_id = %s
                    """,
                    (status, json.dumps(metrics), run_id),
                )
            elif status == "failed":
                cur.execute(
                    """
                    UPDATE backtest_runs
                    SET status = %s, error_message = %s, completed_at = NOW()
                    WHERE run_id = %s
                    """,
                    (status, error, run_id),
                )
            else:
                cur.execute(
                    "UPDATE backtest_runs SET status = %s WHERE run_id = %s",
                    (status, run_id),
                )
    except Exception as e:
        logger.error(f"[BACKTEST] _update_run_status error: {e}")


def _insert_backtest_trade(trade: dict) -> None:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_trades
                    (run_id, bar_index, symbol, action, entry_price, exit_price,
                     stop_loss, target, quantity, pnl, exit_reason, result,
                     entry_bar_time, exit_bar_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trade["run_id"], trade["bar_index"], trade["symbol"],
                    trade["action"], trade["entry_price"], trade["exit_price"],
                    trade["stop_loss"], trade["target"], trade["quantity"],
                    trade["pnl"], trade["exit_reason"], trade["result"],
                    trade.get("entry_bar_time"), trade.get("exit_bar_time"),
                ),
            )
    except Exception as e:
        logger.error(f"[BACKTEST] _insert_backtest_trade error: {e}")


def _insert_equity_curve(run_id: str, equity_curve: list) -> None:
    try:
        with db_cursor() as cur:
            for point in equity_curve:
                cur.execute(
                    """
                    INSERT INTO backtest_equity_curve (run_id, bar_index, bar_time, equity)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (run_id, point["bar_index"], point.get("bar_time"), point["equity"]),
                )
    except Exception as e:
        logger.error(f"[BACKTEST] _insert_equity_curve error: {e}")
