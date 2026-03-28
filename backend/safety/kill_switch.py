from datetime import datetime, timezone
from utils.logger import setup_logger
from db.connection import db_cursor

logger = setup_logger(__name__)


def is_trading_halted() -> bool:
    """Return True if kill switch is active. Fails safe — returns True on DB error."""
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT is_active FROM kill_switch ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            return bool(row[0]) if row else False
    except Exception as e:
        logger.error(f"[KILL_SWITCH] DB error checking kill switch — failing safe: {e}")
        return True


def activate_kill_switch(reason: str, activated_by: str) -> bool:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO kill_switch (is_active, reason, activated_by)
                VALUES (TRUE, %s, %s)
                """,
                (reason, activated_by),
            )
        logger.warning(f"[KILL_SWITCH] ACTIVATED by '{activated_by}': {reason}")
        return True
    except Exception as e:
        logger.error(f"[KILL_SWITCH] Failed to activate: {e}")
        return False


def deactivate_kill_switch() -> bool:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO kill_switch (is_active, reason, activated_by, deactivated_at)
                VALUES (FALSE, 'manual_resume', 'system', %s)
                """,
                (datetime.now(timezone.utc),),
            )
        logger.info("[KILL_SWITCH] Deactivated — trading resumed")
        return True
    except Exception as e:
        logger.error(f"[KILL_SWITCH] Failed to deactivate: {e}")
        return False
