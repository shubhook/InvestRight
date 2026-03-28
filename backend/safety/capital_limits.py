import os
from typing import Tuple
from utils.logger import setup_logger
from db.connection import db_cursor

logger = setup_logger(__name__)

DEFAULT_CAPITAL_LIMIT = float(os.getenv("DEFAULT_CAPITAL_LIMIT", 10.0))


def get_limit(symbol: str) -> float:
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT max_capital_pct FROM capital_limits WHERE symbol = %s",
                (symbol,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else DEFAULT_CAPITAL_LIMIT
    except Exception as e:
        logger.error(f"[CAPITAL] Failed to get limit for {symbol}: {e}")
        return DEFAULT_CAPITAL_LIMIT


def get_current_exposure(symbol: str) -> float:
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT current_exposure_pct FROM capital_limits WHERE symbol = %s",
                (symbol,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"[CAPITAL] Failed to get exposure for {symbol}: {e}")
        return 0.0


def check_limit(symbol: str, proposed_position_size: float) -> Tuple[bool, str]:
    """
    Returns (True, "") if trade fits within limit.
    Returns (False, reason) if it would breach the limit or on DB failure.
    proposed_position_size is a fraction (0.0–1.0).
    """
    if proposed_position_size is None or proposed_position_size == 0.0:
        return True, ""

    if proposed_position_size > 1.0:
        return False, "Invalid position size > 100%"

    proposed_pct = proposed_position_size * 100.0

    try:
        limit = get_limit(symbol)
        current = get_current_exposure(symbol)

        if current + proposed_pct > 100.0:
            return False, (
                f"Total exposure would exceed 100%: "
                f"{symbol} current {current:.1f}% + proposed {proposed_pct:.1f}%"
            )

        if current + proposed_pct > limit:
            return False, (
                f"Capital limit breached: {symbol} current exposure {current:.1f}% "
                f"+ proposed {proposed_pct:.1f}% exceeds {limit:.1f}% limit"
            )

        return True, ""

    except Exception as e:
        logger.error(f"[CAPITAL] DB error during limit check for {symbol}: {e}")
        return False, "Capital limit check failed — DB unavailable"


def update_exposure(symbol: str, position_size: float) -> bool:
    proposed_pct = position_size * 100.0
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO capital_limits (symbol, current_exposure_pct)
                VALUES (%s, %s)
                ON CONFLICT (symbol) DO UPDATE
                SET current_exposure_pct = capital_limits.current_exposure_pct + EXCLUDED.current_exposure_pct,
                    updated_at = NOW()
                """,
                (symbol, proposed_pct),
            )
        logger.info(f"[CAPITAL] Exposure updated for {symbol}: +{proposed_pct:.1f}%")
        return True
    except Exception as e:
        logger.error(f"[CAPITAL] Failed to update exposure for {symbol}: {e}")
        return False


def reset_exposure(symbol: str) -> bool:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO capital_limits (symbol, current_exposure_pct)
                VALUES (%s, 0.0)
                ON CONFLICT (symbol) DO UPDATE
                SET current_exposure_pct = 0.0,
                    updated_at = NOW()
                """,
                (symbol,),
            )
        logger.info(f"[CAPITAL] Exposure reset for {symbol}")
        return True
    except Exception as e:
        logger.error(f"[CAPITAL] Failed to reset exposure for {symbol}: {e}")
        return False
