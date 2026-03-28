from utils.logger import setup_logger
from memory.memory_reader import get_trade, update_trade_result

logger = setup_logger(__name__)

def evaluate(trade_id: str, current_price: float) -> dict:
    """
    Evaluate a past trade against current price to determine if it was correct or wrong.
    
    Args:
        trade_id (str): ID of the trade to evaluate
        current_price (float): Current market price for evaluation
        
    Returns:
        dict: {
            "trade_id": str,
            "result": "correct" | "wrong" | "pending",
            "message": str
        }
    """
    try:
        # Retrieve the trade from memory
        trade = get_trade(trade_id)
        
        if not trade:
            logger.error(f"[FEEDBACK] Trade not found: {trade_id}")
            return {
                "trade_id": trade_id,
                "result": "pending",
                "message": "Trade not found in memory"
            }
        
        # If trade already has a result, return it
        if trade.get("result") in ["correct", "wrong"]:
            logger.info(f"[FEEDBACK] Trade {trade_id} already evaluated: {trade.get('result')}")
            return {
                "trade_id": trade_id,
                "result": trade.get("result"),
                "message": f"Trade already evaluated as {trade.get('result')}"
            }
        
        action = trade.get("action")
        entry = trade.get("entry")
        stop_loss = trade.get("stop_loss")
        target = trade.get("target")
        
        # Validate we have necessary data
        if None in [action, entry, stop_loss, target]:
            logger.error(f"[FEEDBACK] Missing required trade data for {trade_id}")
            return {
                "trade_id": trade_id,
                "result": "pending",
                "message": "Missing required trade data"
            }
        
        # Evaluate based on action type
        result = "pending"
        message = ""
        
        if action == "BUY":
            # For BUY: correct if price >= target, wrong if price <= stop_loss
            if current_price >= target:
                result = "correct"
                message = f"BUY trade hit target: {current_price} >= {target}"
            elif current_price <= stop_loss:
                result = "wrong"
                message = f"BUY trade hit stop loss: {current_price} <= {stop_loss}"
            else:
                result = "pending"
                message = f"BUY trade in progress: {stop_loss} < {current_price} < {target}"
                
        elif action == "SELL":
            # For SELL: correct if price <= target, wrong if price >= stop_loss
            if current_price <= target:
                result = "correct"
                message = f"SELL trade hit target: {current_price} <= {target}"
            elif current_price >= stop_loss:
                result = "wrong"
                message = f"SELL trade hit stop loss: {current_price} >= {stop_loss}"
            else:
                result = "pending"
                message = f"SELL trade in progress: {target} < {current_price} < {stop_loss}"
        
        # Update the trade result in memory
        if result in ["correct", "wrong"]:
            update_success = update_trade_result(trade_id, result)
            if update_success:
                logger.info(f"[FEEDBACK] Trade {trade_id} evaluated as {result}: {message}")
            else:
                logger.error(f"[FEEDBACK] Failed to update trade {trade_id} result")
        else:
            logger.info(f"[FEEDBACK] Trade {trade_id} evaluation pending: {message}")
        
        return {
            "trade_id": trade_id,
            "result": result,
            "message": message
        }

    except Exception as e:
        logger.error(f"[FEEDBACK] Error in feedback agent: {str(e)}")
        return {
            "trade_id": trade_id,
            "result": "pending",
            "message": f"Feedback engine error: {str(e)}"
        }


def record_outcome(trade_id: str, exit_price: float, exit_reason: str) -> dict:
    """
    Record the final outcome of a trade after a confirmed exit.
    Called by exit_monitor after position is closed.

    exit_reason: "target_hit" | "stop_hit" | "manual"

    Returns: {"trade_id": str, "result": "correct"|"wrong"|"pending", "message": str}
    """
    try:
        trade = get_trade(trade_id)
        if not trade:
            logger.error(f"[FEEDBACK] record_outcome: trade not found {trade_id}")
            return {"trade_id": trade_id, "result": "pending", "message": "Trade not found"}

        # Already resolved — don't overwrite
        if trade.get("result") in ("correct", "wrong"):
            return {"trade_id": trade_id, "result": trade["result"],
                    "message": f"Already evaluated as {trade['result']}"}

        action = trade.get("action")

        if exit_reason == "target_hit":
            result = "correct"
        elif exit_reason == "stop_hit":
            result = "wrong"
        else:
            # Manual close — evaluate by price vs entry
            entry     = trade.get("entry") or 0.0
            stop_loss = trade.get("stop_loss") or 0.0
            target    = trade.get("target") or 0.0
            if action == "BUY":
                result = "correct" if exit_price >= target else "wrong" if exit_price <= stop_loss else "pending"
            elif action == "SELL":
                result = "correct" if exit_price <= target else "wrong" if exit_price >= stop_loss else "pending"
            else:
                result = "pending"

        if result in ("correct", "wrong"):
            update_trade_result(trade_id, result)
            logger.info(f"[FEEDBACK] Trade {trade_id} outcome recorded: {result} (exit={exit_reason})")

        return {
            "trade_id": trade_id,
            "result":   result,
            "message":  f"Outcome recorded: {result} via {exit_reason}",
        }
    except Exception as e:
        logger.error(f"[FEEDBACK] record_outcome error: {e}")
        return {"trade_id": trade_id, "result": "pending", "message": str(e)}