import os
import uuid
from datetime import datetime
from utils.logger import setup_logger
from memory.memory_store import store_trade, update_trade_result
from safety.idempotency import generate_key, is_duplicate, record_key
from safety.kill_switch import is_trading_halted
from broker.broker_factory import get_broker
from broker.order_manager import calculate_quantity, submit_order, poll_order_status
from portfolio.position_manager import open_position

logger = setup_logger(__name__)


def execute(decision: dict, symbol: str) -> dict:
    """
    Execute a trading decision end-to-end:
    idempotency check → store trade → place order → poll fill.

    Args:
        decision (dict): Output from risk_engine
        symbol   (str):  Stock symbol (e.g. "RELIANCE.NS")

    Returns:
        dict with keys: executed, trade_id, order_id, broker_order_id,
                        broker_mode, filled_price, filled_quantity,
                        reason, trade_record
    """
    if not symbol:
        raise ValueError("[ACTION] symbol must be provided to execute()")

    try:
        action = decision.get("action")

        if action == "WAIT":
            reason = decision.get("rejection_reason", "No trade executed")
            logger.info(f"[ACTION] {reason}")
            return _no_exec(reason)

        # ------------------------------------------------------------------
        # Idempotency guard
        # ------------------------------------------------------------------
        idem_key = generate_key(symbol, action)
        if is_duplicate(idem_key):
            logger.warning(
                f"[ACTION] Duplicate signal blocked: {symbol} {action} (key={idem_key})"
            )
            return _no_exec("Duplicate signal within 15-min window")

        # ------------------------------------------------------------------
        # Persist trade record
        # ------------------------------------------------------------------
        trade_id = str(uuid.uuid4())
        trade_record = {
            "trade_id":               trade_id,
            "timestamp":              datetime.now().isoformat(),
            "symbol":                 symbol,
            "action":                 action,
            "entry":                  decision.get("entry"),
            "stop_loss":              decision.get("stop_loss"),
            "target":                 decision.get("target"),
            "rr_ratio":               decision.get("rr_ratio"),
            "max_loss_pct":           decision.get("max_loss_pct"),
            "position_size_fraction": decision.get("position_size_fraction"),
            "rejection_reason":       decision.get("rejection_reason"),
            "features_vector":        decision.get("features_vector", {}),
        }

        if not store_trade(trade_record):
            logger.error("[ACTION] Failed to store trade in DB")
            return _no_exec("Failed to store trade in DB")

        # Record idempotency key immediately after successful store
        if not record_key(idem_key, trade_id, symbol, action):
            logger.critical(
                f"[ACTION] Idempotency key NOT recorded for trade {trade_id} — "
                f"next run may not detect duplicate"
            )

        logger.info(
            f"[ACTION] Trade stored: {action} {symbol} @ {decision.get('entry')} "
            f"SL={decision.get('stop_loss')} T={decision.get('target')}"
        )

        # ------------------------------------------------------------------
        # Quantity calculation
        # ------------------------------------------------------------------
        total_capital = float(os.getenv("TOTAL_CAPITAL", 0))
        position_size = decision.get("position_size_fraction") or 0.0
        entry_price   = decision.get("entry") or 0.0

        quantity = calculate_quantity(position_size, entry_price, total_capital)
        if quantity == 0:
            reason = "Insufficient capital for minimum quantity"
            logger.warning(f"[ACTION] {reason} for {symbol} (size={position_size}, entry={entry_price})")
            update_trade_result(trade_id, "wrong")
            return {**_no_exec(reason), "trade_id": trade_id, "trade_record": trade_record}

        # ------------------------------------------------------------------
        # Kill switch — re-check immediately before placing any order
        # ------------------------------------------------------------------
        if is_trading_halted():
            logger.warning(f"[ACTION] Kill switch active mid-execution — cancelling trade {trade_id}")
            update_trade_result(trade_id, "wrong")
            return {**_no_exec("Kill switch activated before order placement"),
                    "trade_id": trade_id, "trade_record": trade_record}

        # ------------------------------------------------------------------
        # Broker execution
        # ------------------------------------------------------------------
        broker = get_broker()
        broker_mode = os.getenv("BROKER_MODE", "paper").lower()

        order_params = {
            "trade_id":   trade_id,
            "symbol":     symbol,
            "action":     action,
            "quantity":   quantity,
            "order_type": "MARKET",
            "price":      None,
            "entry":      decision.get("entry"),
            "stop_loss":  decision.get("stop_loss"),
            "target":     decision.get("target"),
        }

        order_result = submit_order(broker, order_params)

        if order_result.get("status") == "FAILED":
            logger.error(
                f"[ACTION] Trade stored but order placement failed — "
                f"marking trade as failed (trade_id={trade_id})"
            )
            update_trade_result(trade_id, "wrong")
            return {
                "executed":        False,
                "trade_id":        trade_id,
                "order_id":        order_result.get("order_id"),
                "broker_order_id": order_result.get("broker_order_id"),
                "broker_mode":     broker_mode,
                "filled_price":    None,
                "filled_quantity": None,
                "reason":          order_result.get("failure_reason", "Order placement failed"),
                "trade_record":    trade_record,
            }

        # ------------------------------------------------------------------
        # Poll for fill (paper fills immediately; live polls up to 30 s)
        # ------------------------------------------------------------------
        broker_order_id = order_result.get("broker_order_id")
        fill_result     = poll_order_status(broker, broker_order_id, trade_id)

        executed = fill_result.get("status") in ("FILLED", "PARTIAL")
        logger.info(
            f"[ACTION] Order final status: {fill_result.get('status')} — "
            f"filled_price={fill_result.get('filled_price')} "
            f"filled_qty={fill_result.get('filled_quantity')}"
        )

        # ------------------------------------------------------------------
        # Open position on confirmed fill
        # ------------------------------------------------------------------
        position_id = None
        if executed and fill_result.get("filled_price") is not None:
            fill_data = {
                "trade_id":     trade_id,
                "order_id":     order_result.get("order_id"),
                "symbol":       symbol,
                "action":       action,
                "quantity":     fill_result.get("filled_quantity") or quantity,
                "filled_price": fill_result.get("filled_price"),
                "stop_loss":    decision.get("stop_loss"),
                "target":       decision.get("target"),
            }
            position = open_position(fill_data)
            if position:
                position_id = position.get("position_id")
            else:
                logger.critical(
                    f"[ACTION] open_position failed for trade {trade_id} — "
                    f"order filled but no position record created"
                )

        return {
            "executed":        executed,
            "trade_id":        trade_id,
            "order_id":        order_result.get("order_id"),
            "position_id":     position_id,
            "broker_order_id": broker_order_id,
            "broker_mode":     broker_mode,
            "filled_price":    fill_result.get("filled_price"),
            "filled_quantity": fill_result.get("filled_quantity"),
            "reason":          f"Trade executed: {action}" if executed else fill_result.get("failure_reason"),
            "trade_record":    trade_record,
        }

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"[ACTION] Error in action agent: {e}")
        return {
            "executed":        False,
            "trade_id":        None,
            "order_id":        None,
            "position_id":     None,
            "broker_order_id": None,
            "broker_mode":     os.getenv("BROKER_MODE", "paper"),
            "filled_price":    None,
            "filled_quantity": None,
            "reason":          f"Action engine error: {e}",
            "trade_record":    {},
        }


def _no_exec(reason: str) -> dict:
    return {
        "executed":        False,
        "trade_id":        None,
        "order_id":        None,
        "position_id":     None,
        "broker_order_id": None,
        "broker_mode":     os.getenv("BROKER_MODE", "paper"),
        "filled_price":    None,
        "filled_quantity": None,
        "reason":          reason,
        "trade_record":    {},
    }
