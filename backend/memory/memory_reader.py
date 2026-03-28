import json
import os
from datetime import datetime
from utils.logger import setup_logger
from memory.memory_store import _save_memory

logger = setup_logger(__name__)

# Memory file path - same as in memory_store.py
MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory.json")

def _load_memory() -> dict:
    """Load memory from JSON file."""
    if not os.path.exists(MEMORY_FILE):
        return {"trades": {}}
    
    try:
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[MEMORY_READER] Failed to load memory: {str(e)}")
        return {"trades": {}}

def get_trade(trade_id: str) -> dict:
    """
    Retrieve a trade from memory by ID.
    
    Args:
        trade_id (str): ID of the trade to retrieve
        
    Returns:
        dict: Trade data or None if not found
    """
    try:
        memory = _load_memory()
        return memory.get("trades", {}).get(trade_id)
    except Exception as e:
        logger.error(f"[MEMORY_READER] Error retrieving trade {trade_id}: {str(e)}")
        return None

def update_trade_result(trade_id: str, result: str) -> bool:
    """
    Update the result of a trade in memory.
    
    Args:
        trade_id (str): ID of the trade to update
        result (str): Result ("correct" or "wrong")
        
    Returns:
        bool: True if updated successfully, False otherwise
    """
    try:
        if result not in ["correct", "wrong"]:
            logger.error(f"[MEMORY_READER] Invalid result: {result}")
            return False
        
        memory = _load_memory()
        
        if trade_id not in memory.get("trades", {}):
            logger.error(f"[MEMORY_READER] Trade not found: {trade_id}")
            return False
        
        # Update the trade result
        memory["trades"][trade_id]["result"] = result
        memory["trades"][trade_id]["updated_at"] = datetime.now().isoformat()
        
        # Save back to file
        _save_memory(memory)
        
        logger.info(f"[MEMORY_READER] Trade {trade_id} result updated to: {result}")
        return True
        
    except Exception as e:
        logger.error(f"[MEMORY_READER] Error updating trade result: {str(e)}")
        return False

def get_failure_patterns() -> list:
    """
    Get patterns with high failure rate.
    
    Returns:
        List[str]: Patterns with failure rate > 50%
    """
    try:
        memory = _load_memory()
        trades = memory.get("trades", {})
        
        # Group trades by pattern
        pattern_stats = {}
        for trade_id, trade in trades.items():
            pattern = trade.get("pattern", "unknown")
            result = trade.get("result", "pending")
            
            if pattern not in pattern_stats:
                pattern_stats[pattern] = {"total": 0, "wrong": 0}
            
            pattern_stats[pattern]["total"] += 1
            if result == "wrong":
                pattern_stats[pattern]["wrong"] += 1
        
        # Calculate failure rates and return patterns with >50% failure
        failure_patterns = []
        for pattern, stats in pattern_stats.items():
            if stats["total"] > 0:  # Avoid division by zero
                failure_rate = stats["wrong"] / stats["total"]
                if failure_rate > 0.5:
                    failure_patterns.append(pattern)
        
        logger.info(f"[MEMORY_READER] Found failure patterns: {failure_patterns}")
        return failure_patterns
        
    except Exception as e:
        logger.error(f"[MEMORY_READER] Error getting failure patterns: {str(e)}")
        return []

def get_success_rate(pattern: str) -> float:
    """
    Get success rate for a specific pattern.
    
    Args:
        pattern (str): Pattern name
        
    Returns:
        float: Success rate (0.0 to 1.0)
    """
    try:
        memory = _load_memory()
        trades = memory.get("trades", {})
        
        total = 0
        correct = 0
        
        for trade_id, trade in trades.items():
            if trade.get("pattern") == pattern:
                total += 1
                if trade.get("result") == "correct":
                    correct += 1
        
        if total == 0:
            return 0.0
        
        success_rate = correct / total
        logger.info(f"[MEMORY_READER] Success rate for pattern '{pattern}': {success_rate:.2f}")
        return success_rate
        
    except Exception as e:
        logger.error(f"[MEMORY_READER] Error getting success rate for {pattern}: {str(e)}")
        return 0.0

def get_all_trades() -> dict:
    """
    Get all trades from memory.
    
    Returns:
        dict: All trades keyed by trade_id
    """
    try:
        memory = _load_memory()
        return memory.get("trades", {})
    except Exception as e:
        logger.error(f"[MEMORY_READER] Error getting all trades: {str(e)}")
        return {}

