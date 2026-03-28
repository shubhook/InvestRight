"""
Fix 7: Replace hard binary max-loss cap with Kelly-fraction position sizing.

Instead of rejecting any trade whose stop-loss implies more than X% loss,
the engine now:
  1. Computes a Kelly fraction based on P(win) and the actual R:R.
  2. Returns a position_size_fraction (capped at MAX_KELLY_FRACTION).
  3. Only hard-rejects if the Kelly fraction is negative (negative EV) or
     if the max-loss exceeds an extreme safety cap (MAX_LOSS_HARD_CAP).

Kelly fraction (binary bet form):
    K = P(win) - P(loss) / RR
      = p_win - (1 - p_win) / rr_ratio

A positive K means the trade has positive EV and should be taken, sized
proportionally to conviction.  A negative K means the EV is negative — the
trade should be rejected regardless.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger
from safety.capital_limits import check_limit, update_exposure

logger = setup_logger(__name__)

MAX_KELLY_FRACTION = 0.50   # Never risk more than 50% of capital
MAX_LOSS_HARD_CAP  = 0.10   # Absolute hard cap: reject if stop implies >10% loss


def apply_risk(decision: dict, analysis: dict, ohlc: pd.DataFrame, symbol: str = None) -> dict:
    """
    Apply risk management to a proposed trading decision.

    Args:
        decision : dict from decision_agent (action, probability_up, …)
        analysis : dict from analysis_agent (support, resistance, volatility, …)
        ohlc     : pd.DataFrame with OHLCV columns
        symbol   : str — required for capital limit check

    Returns:
        dict:
            action               : "BUY" | "SELL" | "WAIT"
            entry                : float | None
            stop_loss            : float | None
            target               : float | None
            rr_ratio             : float | None
            max_loss_pct         : float | None
            position_size_fraction : float | None  (Fix 7: Kelly-based)
            rejection_reason     : str | None
    """
    _wait = lambda reason, entry=None, sl=None, tgt=None, rr=None, ml=None: {
        "action":                "WAIT",
        "entry":                 entry,
        "stop_loss":             sl,
        "target":                tgt,
        "rr_ratio":              rr,
        "max_loss_pct":          ml,
        "position_size_fraction": None,
        "rejection_reason":      reason,
    }

    try:
        action = decision.get("action")

        if action == "WAIT":
            return _wait(decision.get("reason", "Initial decision was WAIT"))

        if action not in ("BUY", "SELL"):
            return _wait(f"Invalid action: {action}")

        if ohlc is None or ohlc.empty or "close" not in ohlc.columns:
            return _wait("Invalid or missing OHLC data for risk calculation")

        entry_price = float(ohlc["close"].iloc[-1])

        # ------------------------------------------------------------------
        # 1. Stop loss — nearest support (BUY) or resistance (SELL)
        # ------------------------------------------------------------------
        volatility = analysis.get("volatility", 0.0)

        if action == "BUY":
            valid_support = [s for s in analysis.get("support", []) if s < entry_price]
            stop_loss = max(valid_support) if valid_support else entry_price - 2 * volatility
            if not valid_support:
                logger.warning(f"[RISK] No valid support; using volatility stop: {stop_loss:.2f}")
        else:
            valid_res = [r for r in analysis.get("resistance", []) if r > entry_price]
            stop_loss = min(valid_res) if valid_res else entry_price + 2 * volatility
            if not valid_res:
                logger.warning(f"[RISK] No valid resistance; using volatility stop: {stop_loss:.2f}")

        if stop_loss is None or pd.isna(stop_loss):
            return _wait("Failed to calculate stop loss")

        # ------------------------------------------------------------------
        # 2. Risk / target (2 : 1 reward : risk)
        # ------------------------------------------------------------------
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            return _wait("Invalid risk (zero or negative)")

        target  = entry_price + 2 * risk if action == "BUY" else entry_price - 2 * risk
        reward  = abs(target - entry_price)
        rr_ratio = reward / risk  # always 2.0 given the above

        # ------------------------------------------------------------------
        # 3. Max-loss percentage
        # ------------------------------------------------------------------
        max_loss_pct = (risk / entry_price) * 100

        # Extreme hard cap — outright reject (fix 7: raised to 10%)
        if max_loss_pct > MAX_LOSS_HARD_CAP * 100:
            return _wait(
                f"Stop loss implies {max_loss_pct:.2f}% loss, exceeding {MAX_LOSS_HARD_CAP*100:.0f}% hard cap",
                entry=entry_price, sl=stop_loss, tgt=target, rr=rr_ratio, ml=max_loss_pct,
            )

        # ------------------------------------------------------------------
        # 4. Fix 7: Kelly-fraction position sizing
        # ------------------------------------------------------------------
        p_win = float(decision.get("probability_up", 0.5))
        p_loss = 1.0 - p_win

        # Kelly formula for a binary bet: K = P(win) - P(loss)/RR
        kelly = p_win - p_loss / rr_ratio

        if kelly <= 0:
            return _wait(
                f"Kelly fraction is non-positive ({kelly:.3f}) — negative EV trade rejected",
                entry=entry_price, sl=stop_loss, tgt=target, rr=rr_ratio, ml=max_loss_pct,
            )

        # Cap at maximum fraction
        position_size_fraction = min(kelly, MAX_KELLY_FRACTION)

        # ------------------------------------------------------------------
        # 5. Capital limit check
        # ------------------------------------------------------------------
        if symbol:
            ok, cap_reason = check_limit(symbol, position_size_fraction)
            if not ok:
                return _wait(
                    cap_reason,
                    entry=entry_price, sl=stop_loss, tgt=target, rr=rr_ratio, ml=max_loss_pct,
                )
            # Update exposure — log critical warning if DB write fails but do not block
            if not update_exposure(symbol, position_size_fraction):
                logger.critical(
                    f"[RISK] Capital exposure update FAILED for {symbol} — "
                    f"trade will proceed but exposure tracking is inconsistent"
                )

        logger.info(
            f"[RISK] {action} validated: entry={entry_price:.2f}, SL={stop_loss:.2f}, "
            f"target={target:.2f}, RR={rr_ratio:.2f}, max_loss={max_loss_pct:.2f}%, "
            f"kelly={kelly:.3f}, position_size={position_size_fraction:.3f}"
        )

        return {
            "action":                  action,
            "entry":                   entry_price,
            "stop_loss":               stop_loss,
            "target":                  target,
            "rr_ratio":                rr_ratio,
            "max_loss_pct":            max_loss_pct,
            "position_size_fraction":  round(position_size_fraction, 4),
            "rejection_reason":        None,
        }

    except Exception as e:
        logger.error(f"[RISK] Error in risk engine: {str(e)}")
        return _wait(f"Risk engine error: {str(e)}")
