"""
Performance metrics computation for backtesting.
All functions are pure (read-only) — no DB writes.
"""
import math
from typing import List, Optional

import numpy as np

from utils.logger import setup_logger

logger = setup_logger(__name__)


def compute_metrics(trades: list, initial_capital: float) -> dict:
    """
    Compute aggregated performance metrics from a list of completed trades.

    Each trade dict should contain at minimum: pnl (float or None).

    Args:
        trades:          List of trade dicts produced by backtest_engine.
        initial_capital: Starting capital used to compute return %.

    Returns:
        dict with win_rate, sharpe_ratio, max_drawdown_pct, expectancy, etc.
    """
    completed = [t for t in trades if t.get("pnl") is not None]
    if not completed:
        return _empty_metrics(initial_capital)

    pnls    = [float(t["pnl"]) for t in completed]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    win_rate  = len(winners) / len(completed)
    avg_win   = sum(winners) / len(winners) if winners else 0.0
    avg_loss  = sum(losers)  / len(losers)  if losers  else 0.0

    # Build equity curve from sequential P&L
    equity_curve = [initial_capital]
    running = initial_capital
    for pnl in pnls:
        running += pnl
        equity_curve.append(running)

    final_capital = equity_curve[-1]
    total_return  = (final_capital - initial_capital) / initial_capital if initial_capital else 0.0

    sharpe    = compute_sharpe_ratio(equity_curve)
    max_dd    = compute_max_drawdown(equity_curve)
    exp_value = compute_expectancy(trades)

    profit_factor = None
    if avg_loss < 0:
        profit_factor = round(-avg_win / avg_loss, 4)

    return {
        "total_trades":     len(completed),
        "winning_trades":   len(winners),
        "losing_trades":    len(losers),
        "win_rate":         round(win_rate, 4),
        "total_pnl":        round(total_pnl, 2),
        "total_return_pct": round(total_return * 100, 2),
        "avg_win":          round(avg_win, 2),
        "avg_loss":         round(avg_loss, 2),
        "profit_factor":    profit_factor,
        "sharpe_ratio":     round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "expectancy":       round(exp_value, 2),
        "initial_capital":  initial_capital,
        "final_capital":    round(final_capital, 2),
    }


def compute_sharpe_ratio(
    equity_curve: List[float],
    periods_per_year: int = 252,
) -> float:
    """
    Annualised Sharpe ratio computed from step-by-step returns in equity_curve.

    Args:
        equity_curve:     List of capital values (index 0 = initial capital).
        periods_per_year: Trading periods per year for annualisation.

    Returns:
        Annualised Sharpe ratio (float). Returns 0.0 if not computable.
    """
    if len(equity_curve) < 2:
        return 0.0

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)

    if len(returns) < 2:
        return 0.0

    mean_ret = float(np.mean(returns))
    std_ret  = float(np.std(returns, ddof=1))

    if std_ret == 0:
        return 0.0

    return float((mean_ret / std_ret) * math.sqrt(periods_per_year))


def compute_max_drawdown(equity_curve: List[float]) -> float:
    """
    Maximum drawdown as a fraction of peak equity (e.g. 0.15 = 15%).

    Returns:
        float in [0, 1]. 0.0 if not computable.
    """
    if len(equity_curve) < 2:
        return 0.0

    peak   = equity_curve[0]
    max_dd = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

    return float(max_dd)


def compute_expectancy(trades: list) -> float:
    """
    Expected P&L per trade (mean P&L across completed trades).

    Returns:
        float — average P&L. 0.0 if no completed trades.
    """
    completed = [t for t in trades if t.get("pnl") is not None]
    if not completed:
        return 0.0
    return float(sum(t["pnl"] for t in completed) / len(completed))


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _empty_metrics(initial_capital: float) -> dict:
    return {
        "total_trades":     0,
        "winning_trades":   0,
        "losing_trades":    0,
        "win_rate":         0.0,
        "total_pnl":        0.0,
        "total_return_pct": 0.0,
        "avg_win":          0.0,
        "avg_loss":         0.0,
        "profit_factor":    None,
        "sharpe_ratio":     0.0,
        "max_drawdown_pct": 0.0,
        "expectancy":       0.0,
        "initial_capital":  initial_capital,
        "final_capital":    initial_capital,
    }
