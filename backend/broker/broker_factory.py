import os
from broker.base import BaseBroker
from utils.logger import setup_logger

logger = setup_logger(__name__)


def get_broker() -> BaseBroker:
    """
    Return the correct broker for the current BROKER_MODE.
    Creates a fresh instance on every call (Kite token can change mid-session).
    Falls back to PaperBroker if live credentials are missing or init fails.
    """
    from broker.paper_broker import PaperBroker

    mode = os.getenv("BROKER_MODE", "paper").lower()

    if mode not in ("paper", "live"):
        logger.warning(
            f"[BROKER] Unrecognised BROKER_MODE='{mode}' — defaulting to paper trading"
        )
        return PaperBroker()

    if mode == "paper":
        return PaperBroker()

    # mode == "live"
    api_key = os.getenv("KITE_API_KEY")

    # Prefer DB-stored token (set via OAuth); fall back to env var
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not access_token:
        try:
            from auth.kite_token_refresh import get_active_token
            access_token = get_active_token()
        except Exception:
            pass

    if not api_key or not access_token:
        logger.critical(
            "[BROKER] Live mode requested but Kite credentials missing — "
            "falling back to paper trading"
        )
        return PaperBroker()

    try:
        from broker.kite_broker import KiteBroker
        return KiteBroker()
    except Exception as e:
        logger.critical(
            f"[BROKER] KiteBroker init failed ({e}) — falling back to paper trading"
        )
        return PaperBroker()
