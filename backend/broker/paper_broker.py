import uuid
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from broker.base import BaseBroker
from cache.redis_client import get_ltp as _redis_get_ltp, set_ltp as _redis_set_ltp
from db.connection import db_cursor
from utils.logger import setup_logger

logger = setup_logger(__name__)

_BROKER_MODE = "paper"


class PaperBroker(BaseBroker):
    """
    Simulated broker — no real money, no external API calls.
    Orders fill immediately at LTP (or decision price if LTP unavailable).
    """

    def place_order(self, order_params: dict) -> dict:
        action   = order_params.get("action")
        quantity = order_params.get("quantity", 0)
        symbol   = order_params.get("symbol", "")
        trade_id = order_params.get("trade_id")

        if quantity <= 0:
            return self._failed("Quantity must be greater than zero", order_params)

        if action not in ("BUY", "SELL"):
            return self._failed("Invalid action", order_params)

        order_id = str(uuid.uuid4())

        # Limit order simulation: fill at the limit price (entry price from risk engine).
        # Fall back to LTP if price is missing, then to decision entry.
        limit_price = order_params.get("price") or order_params.get("entry")
        ltp = self.get_ltp(symbol)
        fill_price = limit_price or ltp
        if fill_price is None:
            return self._failed("No price available for limit order fill", order_params)
        ltp = fill_price
        if not limit_price:
            logger.warning(
                f"[PAPER] No limit price for {symbol} — filling at LTP {ltp}"
            )

        now = datetime.now(timezone.utc)
        try:
            with db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        order_id, trade_id, symbol, action, order_type,
                        quantity, price, status, filled_quantity, filled_price,
                        broker_order_id, broker_mode, placed_at, filled_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, 'FILLED', %s, %s,
                        %s, 'paper', %s, %s, %s
                    )
                    """,
                    (
                        order_id, trade_id, symbol, action,
                        order_params.get("order_type", "MARKET"),
                        quantity, ltp, quantity, ltp,
                        order_id, now, now, now,
                    ),
                )
            logger.info(
                f"[PAPER] Order FILLED: {action} {quantity}x {symbol} @ {ltp:.2f} "
                f"(order_id={order_id})"
            )
            return {
                "order_id":        order_id,
                "broker_order_id": order_id,
                "status":          "FILLED",
                "filled_price":    ltp,
                "filled_quantity": quantity,
                "failure_reason":  None,
            }
        except Exception as e:
            logger.error(f"[PAPER] Failed to insert order row: {e}")
            return self._failed(f"DB error: {e}", order_params, order_id=order_id)

    def get_order_status(self, broker_order_id: str) -> dict:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "SELECT status, filled_quantity, filled_price FROM orders "
                    "WHERE order_id = %s",
                    (broker_order_id,),
                )
                row = cur.fetchone()
            if row is None:
                return {
                    "broker_order_id": broker_order_id,
                    "status":          "FAILED",
                    "filled_quantity": 0,
                    "filled_price":    None,
                    "failure_reason":  "Order not found",
                }
            return {
                "broker_order_id": broker_order_id,
                "status":          row[0],
                "filled_quantity": row[1] or 0,
                "filled_price":    float(row[2]) if row[2] else None,
                "failure_reason":  None,
            }
        except Exception as e:
            logger.error(f"[PAPER] get_order_status failed: {e}")
            return {
                "broker_order_id": broker_order_id,
                "status":          "FAILED",
                "filled_quantity": 0,
                "filled_price":    None,
                "failure_reason":  str(e),
            }

    def cancel_order(self, broker_order_id: str) -> bool:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE orders SET status='CANCELLED', cancelled_at=%s, updated_at=%s "
                    "WHERE order_id=%s",
                    (datetime.now(timezone.utc), datetime.now(timezone.utc), broker_order_id),
                )
            return True
        except Exception as e:
            logger.error(f"[PAPER] cancel_order failed: {e}")
            return False

    def get_ltp(self, symbol: str) -> Optional[float]:
        # Check shared Redis cache first (60s TTL for LTP)
        cached = _redis_get_ltp(symbol)
        if cached is not None:
            return cached

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m")
            if df.empty:
                return None
            ltp = float(df["Close"].iloc[-1])
            _redis_set_ltp(symbol, ltp, ttl_seconds=60)
            return ltp
        except Exception as e:
            logger.warning(f"[PAPER] LTP fetch failed for {symbol}: {e}")
            return None

    def get_portfolio(self) -> dict:
        """Paper trading has no real account — return empty portfolio."""
        return {
            "holdings":  [],
            "positions": [],
            "error":     None,
            "note":      "Paper trading mode — no real holdings.",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _failed(self, reason: str, order_params: dict, order_id: str = None) -> dict:
        oid = order_id or str(uuid.uuid4())
        try:
            with db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (
                        order_id, trade_id, symbol, action, order_type,
                        quantity, broker_mode, status, failure_reason, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'paper', 'FAILED', %s, NOW())
                    ON CONFLICT (order_id) DO NOTHING
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
            logger.error(f"[PAPER] Could not persist FAILED order row: {db_err}")

        return {
            "order_id":        oid,
            "broker_order_id": oid,
            "status":          "FAILED",
            "filled_price":    None,
            "filled_quantity": 0,
            "failure_reason":  reason,
        }
