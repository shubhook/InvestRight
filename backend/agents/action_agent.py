import uuid
from datetime import datetime
from utils.logger import setup_logger
from memory.memory_store import store_trade
from safety.idempotency import generate_key, is_duplicate, record_key

logger = setup_logger(__name__)


def execute(decision: dict, symbol: str) -> dict:
    """
    Execute the trading decision (simulated for MVP).

    Args:
        decision (dict): Output from risk_engine, containing:
            - action: "BUY" | "SELL" | "WAIT"
            - entry: float | None
            - stop_loss: float | None
            - target: float | None
            - rr_ratio: float | None
            - max_loss_pct: float | None
            - rejection_reason: str | None
        symbol (str): Stock symbol (e.g. "RELIANCE.NS")

    Returns:
        dict: Execution result with trade_id if action was taken
    """
    if not symbol:
        raise ValueError("[ACTION] symbol must be provided to execute()")

    try:
        action = decision.get("action")

        if action == "WAIT":
            reason = decision.get("rejection_reason", "No trade executed")
            logger.info(f"[ACTION] {reason}")
            return {
                "executed": False,
                "trade_id": None,
                "reason": reason,
            }

        # ------------------------------------------------------------------
        # Idempotency check — block duplicate signals within 15-min window
        # ------------------------------------------------------------------
        idem_key = generate_key(symbol, action)
        if is_duplicate(idem_key):
            logger.warning(
                f"[ACTION] Duplicate signal blocked: {symbol} {action} (key={idem_key})"
            )
            return {
                "executed": False,
                "trade_id": None,
                "reason": "Duplicate signal within 15-min window",
            }

        # For BUY or SELL, create a trade record
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

        store_success = store_trade(trade_record)

        if store_success:
            # Record idempotency key — log critical warning if this fails
            if not record_key(idem_key, trade_id, symbol, action):
                logger.critical(
                    f"[ACTION] Idempotency key NOT recorded for trade {trade_id} — "
                    f"next run may not detect duplicate"
                )

            logger.info(f"[ACTION] Trade executed and stored: {action} at {decision.get('entry')}")
            logger.info(f"[ACTION] SL: {decision.get('stop_loss')}, Target: {decision.get('target')}")
            logger.info(f"[ACTION] RR: {decision.get('rr_ratio'):.2f}, Max Loss: {decision.get('max_loss_pct'):.2f}%")

            return {
                "executed": True,
                "trade_id": trade_id,
                "reason": f"Trade executed: {action}",
                "trade_record": trade_record,
            }
        else:
            logger.error("[ACTION] Failed to store trade in memory")
            return {
                "executed": False,
                "trade_id": None,
                "reason": "Failed to store trade in memory",
            }

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"[ACTION] Error in action agent: {str(e)}")
        return {
            "executed": False,
            "trade_id": None,
            "reason": f"Action engine error: {str(e)}",
        }
