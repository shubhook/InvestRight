"""
Zerodha Kite Connect broker implementation.

Known limitations (Batch 3):
- Access token expires daily at 6 AM IST. Manual refresh required.
  Auto-refresh via OAuth is out of scope for Batch 3.
- Only entry market orders are placed. SL/target monitoring is
  handled internally by the Feedback Agent.
- Equity cash segment only. No options, futures, or derivatives.
- MIS (intraday) product type by default; CNC via KITE_PRODUCT env var.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from broker.base import BaseBroker
from db.connection import db_cursor
from safety.kill_switch import activate_kill_switch
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _get_kite():
    """
    Build a fresh KiteConnect instance on every call.

    Token priority:
      1. Active token from kite_tokens DB table (managed by /broker/kite/token endpoint)
      2. KITE_ACCESS_TOKEN environment variable (legacy / manual set)
    """
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise ImportError(
            "kiteconnect package not installed. Run: pip install kiteconnect"
        )

    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise EnvironmentError("KITE_API_KEY must be set for live trading.")

    # Prefer DB-stored token (survives restarts, updated via API)
    access_token = None
    try:
        from auth.kite_token_refresh import get_active_token
        access_token = get_active_token()
    except Exception:
        pass

    # Fall back to env var
    if not access_token:
        access_token = os.getenv("KITE_ACCESS_TOKEN")

    if not access_token:
        raise EnvironmentError(
            "No valid Kite access token found. "
            "POST /broker/kite/token to store one or set KITE_ACCESS_TOKEN."
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def _translate_symbol(symbol: str) -> tuple:
    """
    Convert yfinance symbol to (exchange, tradingsymbol) for Kite.

    "RELIANCE.NS" → ("NSE", "RELIANCE")
    "RELIANCE.BO" → ("BSE", "RELIANCE")
    "RELIANCE"    → ("NSE", "RELIANCE")
    """
    if symbol.endswith(".NS"):
        return "NSE", symbol[:-3]
    if symbol.endswith(".BO"):
        return "BSE", symbol[:-3]
    return "NSE", symbol


class KiteBroker(BaseBroker):
    """Routes orders through Zerodha Kite Connect."""

    def place_order(self, order_params: dict) -> dict:
        from kiteconnect import KiteConnect
        from kiteconnect.exceptions import (
            TokenException, NetworkException, DataException, GeneralException
        )

        action   = order_params.get("action")
        quantity = order_params.get("quantity", 0)
        symbol   = order_params.get("symbol", "")
        trade_id = order_params.get("trade_id")

        if quantity <= 0:
            return self._failed("Quantity must be greater than zero", order_params)
        if action not in ("BUY", "SELL"):
            return self._failed("Invalid action", order_params)

        exchange, tradingsymbol = _translate_symbol(symbol)
        product = os.getenv("KITE_PRODUCT", "MIS")

        try:
            kite = _get_kite()
            transaction_type = (
                kite.TRANSACTION_TYPE_BUY if action == "BUY"
                else kite.TRANSACTION_TYPE_SELL
            )
            broker_order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=kite.ORDER_TYPE_MARKET,
            )
            order_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            with db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        order_id, trade_id, symbol, action, order_type,
                        quantity, broker_order_id, broker_mode, status,
                        placed_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'live', 'PLACED', %s, %s)
                    """,
                    (
                        order_id, trade_id, symbol, action,
                        order_params.get("order_type", "MARKET"),
                        quantity, str(broker_order_id), now, now,
                    ),
                )

            logger.info(
                f"[KITE] Order PLACED: {action} {quantity}x {symbol} "
                f"(broker_order_id={broker_order_id})"
            )
            return {
                "order_id":        order_id,
                "broker_order_id": str(broker_order_id),
                "status":          "PLACED",
                "filled_price":    None,
                "filled_quantity": 0,
                "failure_reason":  None,
            }

        except TokenException as e:
            logger.critical(
                f"[KITE] Token expired/invalid — activating kill switch: {e}"
            )
            if not activate_kill_switch(
                reason=f"Kite token expired: {e}",
                activated_by="kite_broker_auto"
            ):
                logger.critical(
                    "[KITE] activate_kill_switch DB write FAILED — "
                    "trading may continue with expired token. Manual intervention required."
                )
            return self._failed(f"Token expired — kill switch activated: {e}", order_params)

        except NetworkException as e:
            logger.error(f"[KITE] Network error placing order for {symbol}: {e}")
            return self._failed(f"Network error: {e}", order_params)

        except DataException as e:
            msg = str(e)
            if "symbol" in msg.lower() or "instrument" in msg.lower():
                return self._failed(f"Symbol not recognised: {e}", order_params)
            return self._failed(f"Data error: {e}", order_params)

        except GeneralException as e:
            return self._failed(f"Kite error: {e}", order_params)

        except Exception as e:
            logger.error(f"[KITE] Unexpected error placing order: {e}")
            return self._failed(str(e), order_params)

    def get_order_status(self, broker_order_id: str) -> dict:
        from kiteconnect.exceptions import TokenException, NetworkException

        _status_map = {
            "COMPLETE":  "FILLED",
            "OPEN":      "PLACED",
            "CANCELLED": "CANCELLED",
            "REJECTED":  "REJECTED",
        }

        try:
            kite    = _get_kite()
            history = kite.order_history(order_id=broker_order_id)
            if not history:
                return self._status_dict(broker_order_id, "PENDING", 0, None, "No history")

            latest     = history[-1]
            raw_status = latest.get("status", "").upper()
            status     = _status_map.get(raw_status, "PENDING")
            filled_qty = latest.get("filled_quantity", 0)
            avg_price  = latest.get("average_price") or None

            return self._status_dict(broker_order_id, status, filled_qty, avg_price, None)

        except TokenException as e:
            logger.critical(f"[KITE] Token expired during status poll — activating kill switch")
            if not activate_kill_switch(
                reason=f"Kite token expired during poll: {e}",
                activated_by="kite_broker_auto"
            ):
                logger.critical(
                    "[KITE] activate_kill_switch DB write FAILED during poll — "
                    "trading may continue with expired token. Manual intervention required."
                )
            return self._status_dict(broker_order_id, "FAILED", 0, None, f"Token expired: {e}")

        except NetworkException as e:
            return self._status_dict(broker_order_id, "PENDING", 0, None, f"Network error: {e}")

        except Exception as e:
            logger.error(f"[KITE] get_order_status error: {e}")
            return self._status_dict(broker_order_id, "PENDING", 0, None, str(e))

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            kite = _get_kite()
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=broker_order_id)
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE orders SET status='CANCELLED', cancelled_at=%s, updated_at=%s "
                    "WHERE broker_order_id=%s",
                    (datetime.now(timezone.utc), datetime.now(timezone.utc), broker_order_id),
                )
            logger.info(f"[KITE] Order cancelled: {broker_order_id}")
            return True
        except Exception as e:
            logger.error(f"[KITE] cancel_order failed: {e}")
            return False

    def get_portfolio(self) -> dict:
        """
        Fetch live holdings and intraday positions from Zerodha.

        Returns:
            {
                "holdings": [...],   # Delivery holdings (CNC)
                "positions": [...],  # Intraday positions (MIS/day)
                "error": None | str
            }
        """
        try:
            kite = _get_kite()
            holdings  = kite.holdings()  or []
            positions = (kite.positions() or {}).get("day", [])

            def _fmt_holding(h):
                return {
                    "symbol":          h.get("tradingsymbol", ""),
                    "exchange":        h.get("exchange", "NSE"),
                    "quantity":        h.get("quantity", 0),
                    "avg_price":       float(h.get("average_price") or 0),
                    "last_price":      float(h.get("last_price") or 0),
                    "pnl":             float(h.get("pnl") or 0),
                    "day_change_pct":  float(h.get("day_change_percentage") or 0),
                }

            def _fmt_position(p):
                return {
                    "symbol":      p.get("tradingsymbol", ""),
                    "exchange":    p.get("exchange", "NSE"),
                    "quantity":    p.get("quantity", 0),
                    "avg_price":   float(p.get("average_price") or 0),
                    "last_price":  float(p.get("last_price") or 0),
                    "pnl":         float(p.get("pnl") or 0),
                    "product":     p.get("product", ""),
                }

            return {
                "holdings":  [_fmt_holding(h) for h in holdings],
                "positions": [_fmt_position(p) for p in positions],
                "error":     None,
            }

        except Exception as e:
            logger.warning(f"[KITE] get_portfolio failed: {e}")
            return {"holdings": [], "positions": [], "error": str(e)}

    def get_ltp(self, symbol: str) -> Optional[float]:
        exchange, tradingsymbol = _translate_symbol(symbol)
        instrument = f"{exchange}:{tradingsymbol}"
        try:
            kite = _get_kite()
            data = kite.ltp([instrument])
            return float(data[instrument]["last_price"])
        except Exception as e:
            logger.warning(f"[KITE] LTP fetch failed for {symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _failed(self, reason: str, order_params: dict) -> dict:
        oid = str(uuid.uuid4())
        try:
            with db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        order_id, trade_id, symbol, action, order_type,
                        quantity, broker_mode, status, failure_reason, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'live', 'FAILED', %s, NOW())
                    """,
                    (
                        oid,
                        order_params.get("trade_id"),
                        order_params.get("symbol", ""),
                        order_params.get("action", ""),
                        order_params.get("order_type", "MARKET"),
                        max(order_params.get("quantity", 0), 0),
                        reason,
                    ),
                )
        except Exception as db_err:
            logger.error(f"[KITE] Could not persist FAILED order row: {db_err}")

        return {
            "order_id":        oid,
            "broker_order_id": oid,
            "status":          "FAILED",
            "filled_price":    None,
            "filled_quantity": 0,
            "failure_reason":  reason,
        }

    @staticmethod
    def _status_dict(bid, status, filled_qty, filled_price, reason):
        return {
            "broker_order_id": bid,
            "status":          status,
            "filled_quantity": filled_qty,
            "filled_price":    float(filled_price) if filled_price else None,
            "failure_reason":  reason,
        }
