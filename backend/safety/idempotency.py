import time
from utils.logger import setup_logger
from db.connection import db_cursor

logger = setup_logger(__name__)

_BUCKET_SECONDS = 900  # 15-minute window


def generate_key(symbol: str, action: str) -> str:
    bucket = int(time.time()) // _BUCKET_SECONDS
    return f"{symbol}:{action}:{bucket}"


def is_duplicate(key: str) -> bool:
    """Return True if key already exists. Fails open — returns False on DB error."""
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT 1 FROM idempotency_log WHERE idempotency_key = %s",
                (key,),
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"[IDEMPOTENCY] DB error checking key '{key}' — failing open: {e}")
        return False


def record_key(key: str, trade_id: str, symbol: str, action: str) -> bool:
    if not trade_id:
        logger.error(f"[IDEMPOTENCY] Cannot record key '{key}' — trade_id is None")
        return False
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO idempotency_log (idempotency_key, trade_id, symbol, action)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (key, trade_id, symbol, action),
            )
        return True
    except Exception as e:
        logger.error(f"[IDEMPOTENCY] Failed to record key '{key}': {e}")
        return False
