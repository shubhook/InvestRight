import json
import os
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Memory file path
MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory.json")

def _load_memory() -> dict:
    """Load memory from JSON file."""
    if not os.path.exists(MEMORY_FILE):
        return {"trades": {}}
    
    try:
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[MEMORY_STORE] Failed to load memory: {str(e)}")
        return {"trades": {}}

def _save_memory(memory_data: dict):
    """Save memory to JSON file."""
    try:
        with open(MEMORY_FILE, 'w') as f:
            json.dump(memory_data, f, indent=2)
    except Exception as e:
        logger.error(f"[MEMORY_STORE] Failed to save memory: {str(e)}")

def store_trade(trade_record: dict) -> bool:
    """
    Store a trade record in memory.
    
    Args:
        trade_record (dict): Trade data to store
        
    Returns:
        bool: True if stored successfully, False otherwise
    """
    try:
        memory = _load_memory()
        
        # Ensure trade_id exists
        trade_id = trade_record.get("trade_id")
        if not trade_id:
            logger.error("[MEMORY_STORE] Trade record missing trade_id")
            return False
        
        # Store the trade
        memory["trades"][trade_id] = trade_record
        
        # Save back to file
        _save_memory(memory)
        
        logger.info(f"[MEMORY_STORE] Trade stored: {trade_id}")
        return True
        
    except Exception as e:
        logger.error(f"[MEMORY_STORE] Error storing trade: {str(e)}")
        return False

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
        logger.error(f"[MEMORY_STORE] Error retrieving trade {trade_id}: {str(e)}")
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
            logger.error(f"[MEMORY_STORE] Invalid result: {result}")
            return False
        
        memory = _load_memory()
        
        if trade_id not in memory.get("trades", {}):
            logger.error(f"[MEMORY_STORE] Trade not found: {trade_id}")
            return False
        
        # Update the trade result
        memory["trades"][trade_id]["result"] = result
        memory["trades"][trade_id]["updated_at"] = datetime.now().isoformat()
        
        # Save back to file
        _save_memory(memory)
        
        logger.info(f"[MEMORY_STORE] Trade {trade_id} result updated to: {result}")
        return True
        
    except Exception as e:
        logger.error(f"[MEMORY_STORE] Error updating trade result: {str(e)}")
        return False

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
        logger.error(f"[MEMORY_STORE] Error getting all trades: {str(e)}")
        return {}