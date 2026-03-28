import os
from datetime import datetime, timezone
from typing import Optional
from db.connection import db_cursor
from utils.logger import setup_logger

logger = setup_logger(__name__)


def initialise() -> bool:
    """
    Seed capital_account from TOTAL_CAPITAL env var on first run.
    Idempotent — does nothing if a row already exists.
    """
    total = float(os.getenv("TOTAL_CAPITAL", 0))
    if total <= 0:
        raise EnvironmentError("TOTAL_CAPITAL must be set and greater than zero.")
    try:
        with db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM capital_account")
            count = cur.fetchone()[0]
            if count == 0:
                cur.execute(
                    """
                    INSERT INTO capital_account
                        (total_capital, deployed_capital, available_capital, realised_pnl)
                    VALUES (%s, 0.00, %s, 0.00)
                    """,
                    (total, total),
                )
                logger.info(f"[CAPITAL_ACCOUNT] Seeded with ₹{total:,.2f}")
            else:
                logger.info("[CAPITAL_ACCOUNT] Already initialised — skipping seed")
        return True
    except Exception as e:
        logger.error(f"[CAPITAL_ACCOUNT] Initialisation failed: {e}")
        return False


def get_account() -> Optional[dict]:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT total_capital, deployed_capital, available_capital,
                       realised_pnl, updated_at
                FROM capital_account
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "total_capital":     float(row[0]),
            "deployed_capital":  float(row[1]),
            "available_capital": float(row[2]),
            "realised_pnl":      float(row[3]),
            "updated_at":        row[4].isoformat() if row[4] else None,
        }
    except Exception as e:
        logger.error(f"[CAPITAL_ACCOUNT] get_account failed: {e}")
        return None


def deploy_capital(amount: float, symbol: str) -> bool:
    if amount <= 0:
        logger.error(f"[CAPITAL_ACCOUNT] deploy_capital called with non-positive amount: {amount}")
        return False
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT total_capital, deployed_capital, available_capital, realised_pnl
                FROM capital_account ORDER BY updated_at DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                logger.error("[CAPITAL_ACCOUNT] No account row found during deploy")
                return False

            total, deployed, available, realised = (float(v) for v in row)

            if amount > available:
                logger.warning(
                    f"[CAPITAL_ACCOUNT] Insufficient capital for {symbol}: "
                    f"requested ₹{amount:,.2f}, available ₹{available:,.2f}"
                )
                return False

            new_deployed  = deployed + amount
            new_available = available - amount

            if new_available < 0:
                logger.warning(
                    f"[CAPITAL_ACCOUNT] available_capital would go negative for {symbol}"
                )

            cur.execute(
                """
                INSERT INTO capital_account
                    (total_capital, deployed_capital, available_capital, realised_pnl)
                VALUES (%s, %s, %s, %s)
                """,
                (total, new_deployed, new_available, realised),
            )

        logger.info(
            f"[CAPITAL_ACCOUNT] Deployed ₹{amount:,.2f} for {symbol}. "
            f"Available: ₹{new_available:,.2f}"
        )
        return True
    except Exception as e:
        logger.error(f"[CAPITAL_ACCOUNT] deploy_capital failed: {e}")
        return False


def release_capital(amount: float, realised_pnl: float) -> bool:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT total_capital, deployed_capital, available_capital, realised_pnl
                FROM capital_account ORDER BY updated_at DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                logger.error("[CAPITAL_ACCOUNT] No account row found during release")
                return False

            total, deployed, available, prev_realised = (float(v) for v in row)

            if amount > deployed:
                logger.critical(
                    f"[CAPITAL_ACCOUNT] release amount ₹{amount:,.2f} > deployed "
                    f"₹{deployed:,.2f} — accounting error, proceeding anyway"
                )

            new_deployed  = max(deployed - amount, 0.0)
            new_available = available + amount + realised_pnl
            new_total     = total + realised_pnl
            new_realised  = prev_realised + realised_pnl

            cur.execute(
                """
                INSERT INTO capital_account
                    (total_capital, deployed_capital, available_capital, realised_pnl)
                VALUES (%s, %s, %s, %s)
                """,
                (new_total, new_deployed, new_available, new_realised),
            )

        logger.info(
            f"[CAPITAL_ACCOUNT] Released ₹{amount:,.2f}, P&L={realised_pnl:+.2f}. "
            f"Total: ₹{new_total:,.2f}, Available: ₹{new_available:,.2f}"
        )
        return True
    except Exception as e:
        logger.error(f"[CAPITAL_ACCOUNT] release_capital failed: {e}")
        return False


def get_available_capital() -> float:
    acct = get_account()
    if acct is None:
        return 0.0
    return acct.get("available_capital", 0.0)


def get_deployed_capital() -> float:
    acct = get_account()
    if acct is None:
        return 0.0
    return acct.get("deployed_capital", 0.0)
