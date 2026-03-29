"""
LLM portfolio narrative summary agent.
Generates human-readable portfolio summaries on demand.
Uses llama-3.3-70b-versatile (Groq) for quality output.
Falls back to numeric string when LLM is unavailable.
"""
import logging
from typing import Optional

from llm.llm_client import call_llm

_logger = logging.getLogger("summary_agent")

_SYSTEM_PROMPT = (
    "You are a quantitative portfolio manager summarising automated "
    "trading system performance. Be factual, specific, and use numbers. "
    "Maximum 4 sentences. Write in third person (the system, not I/we)."
)

_DAILY_SYSTEM_PROMPT = (
    "You are a quantitative analyst writing a daily trading brief. "
    "Be concise, use numbers, and highlight key events. "
    "Maximum 3 sentences."
)


def generate_portfolio_summary(
    portfolio_data: dict,
    trade_history: list,
    timeframe: str = "7d",
    trace_id: Optional[str] = None,
) -> str:
    """
    Generate a narrative portfolio performance summary.

    Args:
        portfolio_data: Output from pnl_calculator.get_portfolio_summary().
        trade_history:  List of completed trade dicts.
        timeframe:      "1d" | "7d" | "30d" | "all"
        trace_id:       Pipeline trace UUID.

    Returns:
        String summary. Falls back to numeric summary on LLM failure.
    """
    if not trade_history:
        return _numeric_fallback(portfolio_data, timeframe)

    pnl_data     = portfolio_data.get("pnl", {})
    capital_data = portfolio_data.get("capital", {})
    stats        = portfolio_data.get("trade_stats", {})

    total_return = pnl_data.get("total_return_pct", 0.0)
    win_rate     = round(float(stats.get("win_rate", 0.0)) * 100, 1)
    total_trades = stats.get("total_trades", 0)
    open_count   = portfolio_data.get("open_positions_count", 0)
    sharpe       = pnl_data.get("sharpe_ratio", 0.0)
    max_dd       = pnl_data.get("max_drawdown_pct", 0.0)

    # Find best and worst trades
    completed = [t for t in trade_history if t.get("result") in ("correct", "wrong")]
    best_trade  = max(completed, key=lambda t: float(t.get("pnl") or 0), default=None)
    worst_trade = min(completed, key=lambda t: float(t.get("pnl") or 0), default=None)

    best_sym  = best_trade["symbol"]  if best_trade  else "N/A"
    best_pnl  = best_trade.get("pnl", 0) if best_trade else 0
    worst_sym = worst_trade["symbol"] if worst_trade else "N/A"
    worst_pnl = worst_trade.get("pnl", 0) if worst_trade else 0

    # Pattern analysis
    pattern_wins  = {}
    pattern_total = {}
    for t in completed:
        fv  = t.get("features_vector") or {}
        pat = t.get("pattern") or fv.get("pattern", "unknown")
        pattern_total[pat] = pattern_total.get(pat, 0) + 1
        if t.get("result") == "correct":
            pattern_wins[pat] = pattern_wins.get(pat, 0) + 1

    pattern_rates = {
        p: pattern_wins.get(p, 0) / pattern_total[p]
        for p in pattern_total
        if pattern_total[p] >= 2
    }
    sorted_patterns = sorted(pattern_rates.items(), key=lambda x: x[1], reverse=True)
    top_patterns  = ", ".join(p for p, _ in sorted_patterns[:2]) if sorted_patterns else "N/A"
    weak_patterns = ", ".join(p for p, _ in sorted_patterns[-2:] if _ < 0.5) if sorted_patterns else "N/A"

    user_prompt = (
        f"Summarise this trading system's performance:\n\n"
        f"Period: {timeframe}\n"
        f"Total Return: {total_return:.2f}%\n"
        f"Win Rate: {win_rate}%\n"
        f"Total Trades: {total_trades}\n"
        f"Best Trade: {best_sym} +₹{best_pnl}\n"
        f"Worst Trade: {worst_sym} ₹{worst_pnl}\n"
        f"Open Positions: {open_count}\n"
        f"Sharpe Ratio: {sharpe:.3f}\n"
        f"Max Drawdown: {max_dd:.2f}%\n\n"
        f"Top performing patterns: {top_patterns}\n"
        f"Underperforming patterns: {weak_patterns}\n\n"
        "Write a 3-4 sentence portfolio summary."
    )

    raw = call_llm(
        prompt=user_prompt,
        system=_SYSTEM_PROMPT,
        model="llama-3.3-70b-versatile",
        max_tokens=300,
        trace_id=trace_id,
        agent_name="summary_agent",
    )

    if raw and raw.strip():
        return raw.strip()

    return _numeric_fallback(portfolio_data, timeframe)


def generate_daily_brief(
    date: str,
    daily_pnl: dict,
    open_positions: list,
    trace_id: Optional[str] = None,
) -> str:
    """
    Generate a daily trading brief.

    Args:
        date:           Date string (e.g. "2026-01-15").
        daily_pnl:      Output from pnl_calculator.get_daily_pnl().
        open_positions: List of open position dicts.
        trace_id:       Pipeline trace UUID.

    Returns:
        String daily brief.
    """
    realised   = daily_pnl.get("realised_pnl", 0.0)
    unrealised = daily_pnl.get("unrealised_pnl", 0.0)
    trades_today = daily_pnl.get("trades_today", 0)
    open_count   = len(open_positions)

    symbols_open = ", ".join(p.get("symbol", "") for p in open_positions[:5])

    user_prompt = (
        f"Daily Trading Brief — {date}\n\n"
        f"Realised P&L Today: ₹{realised:.2f}\n"
        f"Unrealised P&L: ₹{unrealised:.2f}\n"
        f"Trades Executed Today: {trades_today}\n"
        f"Open Positions: {open_count} ({symbols_open})\n\n"
        "Write a 2-3 sentence daily brief."
    )

    raw = call_llm(
        prompt=user_prompt,
        system=_DAILY_SYSTEM_PROMPT,
        model="llama-3.3-70b-versatile",
        max_tokens=200,
        trace_id=trace_id,
        agent_name="summary_agent",
    )

    if raw and raw.strip():
        return raw.strip()

    return (
        f"Daily brief for {date}: "
        f"Realised P&L ₹{realised:.2f}, "
        f"unrealised ₹{unrealised:.2f}, "
        f"{trades_today} trades executed, "
        f"{open_count} positions open."
    )


def _numeric_fallback(portfolio_data: dict, timeframe: str) -> str:
    """Build a plain-text summary from numbers without LLM."""
    pnl     = portfolio_data.get("pnl", {})
    capital = portfolio_data.get("capital", {})
    stats   = portfolio_data.get("trade_stats", {})
    total_return = pnl.get("total_return_pct", 0.0)
    win_rate     = round(float(stats.get("win_rate", 0.0)) * 100, 1)
    total_trades = stats.get("total_trades", 0)
    available    = capital.get("available_capital", 0.0)
    return (
        f"Period: {timeframe}. "
        f"Total return: {total_return:.2f}%. "
        f"Win rate: {win_rate}% over {total_trades} trades. "
        f"Available capital: ₹{available:,.2f}."
    )
