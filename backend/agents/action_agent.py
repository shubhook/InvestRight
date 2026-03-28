import json
import uuid
from datetime import datetime
from utils.logger import setup_logger
from memory.memory_store import store_trade

logger = setup_logger(__name__)

def execute(decision: dict) -> dict:
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
            
    Returns:
        dict: Execution result with trade_id if action was taken
    """
    try:
        action = decision.get("action")
        
        if action == "WAIT":
            reason = decision.get("rejection_reason", "No trade executed")
            logger.info(f"[ACTION] {reason}")
            return {
                "executed": False,
                "trade_id": None,
                "reason": reason
            }
        
        # For BUY or SELL, create a trade record
        trade_id = str(uuid.uuid4())
        
        trade_record = {
            "trade_id":              trade_id,
            "timestamp":             datetime.now().isoformat(),
            "action":                action,
            "entry":                 decision.get("entry"),
            "stop_loss":             decision.get("stop_loss"),
            "target":                decision.get("target"),
            "rr_ratio":              decision.get("rr_ratio"),
            "max_loss_pct":          decision.get("max_loss_pct"),
            "position_size_fraction": decision.get("position_size_fraction"),
            "rejection_reason":      decision.get("rejection_reason"),
            # Fix 10: persist feature vector so weight learning can use this trade
            "features_vector":       decision.get("features_vector", {}),
        }
        
        # Store the trade in memory
        store_success = store_trade(trade_record)
        
        if store_success:
            logger.info(f"[ACTION] Trade executed and stored: {action} at {decision.get('entry')}")
            logger.info(f"[ACTION] SL: {decision.get('stop_loss')}, Target: {decision.get('target')}")
            logger.info(f"[ACTION] RR: {decision.get('rr_ratio'):.2f}, Max Loss: {decision.get('max_loss_pct'):.2f}%")
            
            return {
                "executed": True,
                "trade_id": trade_id,
                "reason": f"Trade executed: {action}",
                "trade_record": trade_record
            }
        else:
            logger.error("[ACTION] Failed to store trade in memory")
            return {
                "executed": False,
                "trade_id": None,
                "reason": "Failed to store trade in memory"
            }
            
    except Exception as e:
        logger.error(f"[ACTION] Error in action agent: {str(e)}")
        return {
            "executed": False,
            "trade_id": None,
            "reason": f"Action engine error: {str(e)}"
        }