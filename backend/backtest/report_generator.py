"""
Report generator — reads from backtest_* tables only.
All functions are read-only with respect to the database.
"""
import decimal
import json
from typing import Optional

from db.connection import db_cursor
from utils.logger import setup_logger

logger = setup_logger(__name__)


def generate_summary(run_id: str) -> Optional[dict]:
    """
    Return a full summary for a single backtest run.

    Reads from backtest_runs and backtest_trades.
    Returns None if the run does not exist.
    """
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT run_id, symbol, start_date, end_date, interval,
                       initial_capital, status, metrics, error_message,
                       created_at, completed_at
                FROM backtest_runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()

        if row is None:
            return None

        cols = [
            "run_id", "symbol", "start_date", "end_date", "interval",
            "initial_capital", "status", "metrics", "error_message",
            "created_at", "completed_at",
        ]
        summary = _serialize_row(dict(zip(cols, row)))
        summary["trade_breakdown"] = get_trade_breakdown(run_id)
        return summary

    except Exception as e:
        logger.error(f"[REPORT] generate_summary error: {e}")
        return None


def generate_comparison(run_ids: list) -> dict:
    """
    Compare multiple backtest runs side-by-side, ranked by total_return_pct.

    Args:
        run_ids: List of run_id strings.

    Returns:
        dict with 'runs' list (sorted, best first) and 'count'.
    """
    results = []
    for run_id in run_ids:
        summary = generate_summary(run_id)
        if not summary:
            continue

        metrics = summary.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        results.append({
            "run_id":           run_id,
            "symbol":           summary.get("symbol"),
            "start_date":       summary.get("start_date"),
            "end_date":         summary.get("end_date"),
            "status":           summary.get("status"),
            "total_return_pct": metrics.get("total_return_pct"),
            "sharpe_ratio":     metrics.get("sharpe_ratio"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "win_rate":         metrics.get("win_rate"),
            "total_trades":     metrics.get("total_trades"),
        })

    results.sort(
        key=lambda r: r.get("total_return_pct") or float("-inf"),
        reverse=True,
    )
    return {"runs": results, "count": len(results)}


def get_trade_breakdown(run_id: str) -> list:
    """
    Return all trades for a run ordered by bar_index.
    """
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id, bar_index, symbol, action, entry_price, exit_price,
                       stop_loss, target, quantity, pnl, exit_reason, result,
                       entry_bar_time, exit_bar_time
                FROM backtest_trades
                WHERE run_id = %s
                ORDER BY bar_index ASC
                """,
                (run_id,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        return [_serialize_row(dict(zip(cols, row))) for row in rows]

    except Exception as e:
        logger.error(f"[REPORT] get_trade_breakdown error: {e}")
        return []


def list_runs(limit: int = 50, offset: int = 0) -> dict:
    """
    Paginated list of all backtest runs, most recent first.
    """
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT run_id, symbol, start_date, end_date, interval,
                       initial_capital, status, metrics, created_at, completed_at
                FROM backtest_runs
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

            cur.execute("SELECT COUNT(*) FROM backtest_runs")
            total = cur.fetchone()[0]

        runs = [_serialize_row(dict(zip(cols, row))) for row in rows]
        return {"runs": runs, "total": total, "limit": limit, "offset": offset}

    except Exception as e:
        logger.error(f"[REPORT] list_runs error: {e}")
        return {"runs": [], "total": 0, "limit": limit, "offset": offset}


def get_equity_curve(run_id: str) -> list:
    """Return equity curve data points for a run, ordered by bar_index."""
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT bar_index, bar_time, equity
                FROM backtest_equity_curve
                WHERE run_id = %s
                ORDER BY bar_index ASC
                """,
                (run_id,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        return [_serialize_row(dict(zip(cols, row))) for row in rows]

    except Exception as e:
        logger.error(f"[REPORT] get_equity_curve error: {e}")
        return []


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _serialize_row(d: dict) -> dict:
    """Convert Decimal, datetime, and UUID objects to JSON-serialisable types."""
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = None
        elif isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ == "UUID":
            out[k] = str(v)
        else:
            out[k] = v
    return out
