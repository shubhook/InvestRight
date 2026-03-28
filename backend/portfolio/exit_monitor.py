"""
Exit monitor — runs every 15 minutes alongside the analysis scheduler.
Checks all open positions for SL/target hits and closes them.

Kill switch blocks ENTRY, not EXIT — exits always run.
"""

from typing import Optional
from broker.broker_factory import get_broker
from broker.order_manager import submit_order, poll_order_status
from portfolio.position_manager import (
    get_open_positions, close_position, update_current_prices
)
from agents.feedback_agent import record_outcome
from utils.logger import setup_logger

logger = setup_logger(__name__)


def run_exit_checks() -> dict:
    """
    Check all open positions for SL/target hits.
    Returns summary: {checked, exited, errors, details}
    """
    positions = get_open_positions()
    if not positions:
        return {"checked": 0, "exited": 0, "errors": 0, "details": []}

    broker = get_broker()

    # Fetch LTP once per symbol (avoid duplicate API calls)
    symbols     = list({p["symbol"] for p in positions})
    ltp_cache   = {}
    price_map   = {}

    for symbol in symbols:
        ltp = broker.get_ltp(symbol)
        if ltp is not None:
            ltp_cache[symbol] = ltp
            price_map[symbol] = ltp
        else:
            logger.warning(f"[EXIT_MONITOR] LTP unavailable for {symbol} — skipping")

    # Update all current prices in bulk
    if price_map:
        update_current_prices(price_map)

    checked = 0
    exited  = 0
    errors  = 0
    details = []

    for position in positions:
        position_id = position["position_id"]
        symbol      = position["symbol"]

        # Re-check status in case another process already closed it
        if position.get("status") == "closed":
            continue

        checked += 1
        ltp = ltp_cache.get(symbol)

        if ltp is None:
            errors += 1
            details.append({
                "position_id": position_id,
                "symbol":      symbol,
                "exit_reason": None,
                "exit_price":  None,
                "pnl":         None,
            })
            continue

        exit_reason = check_position(position, ltp)

        if exit_reason is None:
            details.append({
                "position_id": position_id,
                "symbol":      symbol,
                "exit_reason": None,
                "exit_price":  None,
                "pnl":         None,
            })
            continue

        logger.info(
            f"[EXIT_MONITOR] {exit_reason} triggered for {symbol} @ {ltp:.2f} "
            f"(position={position_id})"
        )

        success = execute_exit(position, ltp, exit_reason)
        if success:
            exited += 1
            action   = position["action"]
            entry    = float(position["entry_price"])
            qty      = int(position["quantity"])
            pnl      = (ltp - entry) * qty if action == "BUY" else (entry - ltp) * qty
            details.append({
                "position_id": position_id,
                "symbol":      symbol,
                "exit_reason": exit_reason,
                "exit_price":  ltp,
                "pnl":         round(pnl, 2),
            })
        else:
            errors += 1
            details.append({
                "position_id": position_id,
                "symbol":      symbol,
                "exit_reason": exit_reason,
                "exit_price":  ltp,
                "pnl":         None,
            })

    logger.info(
        f"[EXIT_MONITOR] Cycle complete: checked={checked} exited={exited} errors={errors}"
    )
    return {"checked": checked, "exited": exited, "errors": errors, "details": details}


def check_position(position: dict, current_price: float) -> Optional[str]:
    """
    Return exit reason if SL or target is hit, None otherwise.
    Uses >= / <= so exact price equality triggers exit.
    """
    action    = position["action"]
    stop_loss = float(position["stop_loss"])
    target    = float(position["target"])

    if action == "BUY":
        if current_price >= target:
            return "target_hit"
        if current_price <= stop_loss:
            return "stop_hit"
    elif action == "SELL":
        if current_price <= target:
            return "target_hit"
        if current_price >= stop_loss:
            return "stop_hit"
    return None


def execute_exit(position: dict, exit_price: float, exit_reason: str) -> bool:
    """
    Place the exit order, close position in DB, and record trade outcome.
    Returns True only when position is confirmed closed.
    Does NOT close position if exit order fails.
    """
    position_id = position["position_id"]
    symbol      = position["symbol"]
    action      = position["action"]
    quantity    = int(position["quantity"])
    trade_id    = position.get("trade_id")

    # Exit is opposite of entry
    exit_action = "SELL" if action == "BUY" else "BUY"

    broker = get_broker()
    order_params = {
        "trade_id":   trade_id,
        "symbol":     symbol,
        "action":     exit_action,
        "quantity":   quantity,
        "order_type": "MARKET",
        "price":      None,
        "entry":      exit_price,
        "stop_loss":  0,
        "target":     0,
    }

    order_result = submit_order(broker, order_params)
    if order_result.get("status") == "FAILED":
        logger.critical(
            f"[EXIT_MONITOR] Exit order FAILED for {symbol} position {position_id} — "
            f"position remains open. Manual intervention required."
        )
        return False

    broker_order_id = order_result.get("broker_order_id")
    fill_result     = poll_order_status(broker, broker_order_id, trade_id)

    if fill_result.get("status") not in ("FILLED", "PARTIAL"):
        logger.critical(
            f"[EXIT_MONITOR] Exit order not filled for {symbol} position {position_id} "
            f"(status={fill_result.get('status')}) — position remains open"
        )
        return False

    actual_exit_price = fill_result.get("filled_price") or exit_price

    # Close position in DB
    closed = close_position(position_id, actual_exit_price, exit_reason)
    if closed is None:
        logger.error(f"[EXIT_MONITOR] close_position failed for {position_id}")
        return False

    # Record trade outcome for weight learning
    if trade_id:
        record_outcome(trade_id, actual_exit_price, exit_reason)

    return True
