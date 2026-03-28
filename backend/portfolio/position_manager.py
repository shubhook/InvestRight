from datetime import datetime, timezone
from typing import Optional
from db.connection import db_cursor
from portfolio.capital_account import deploy_capital, release_capital
from safety.capital_limits import reset_exposure
from utils.logger import setup_logger

logger = setup_logger(__name__)


def open_position(fill_data: dict) -> Optional[dict]:
    """
    Open a position after a confirmed fill.
    Deploys capital atomically — if deploy fails, position is NOT inserted.

    fill_data keys: trade_id, order_id, symbol, action, quantity,
                    filled_price, stop_loss, target
    """
    trade_id     = fill_data.get("trade_id")
    order_id     = fill_data.get("order_id")
    symbol       = fill_data.get("symbol")
    action       = fill_data.get("action")
    quantity     = fill_data.get("quantity")
    filled_price = fill_data.get("filled_price")
    stop_loss    = fill_data.get("stop_loss")
    target       = fill_data.get("target")

    if filled_price is None:
        logger.error(f"[POSITION] Cannot open position — filled_price is None (trade={trade_id})")
        return None

    # Guard: don't duplicate position for same trade_id
    existing = get_position_by_trade_id(trade_id)
    if existing:
        logger.info(f"[POSITION] Position already exists for trade {trade_id} — returning existing")
        return existing

    capital_deployed = float(filled_price) * int(quantity)

    # Deploy capital first — if this fails, do not insert position
    if not deploy_capital(capital_deployed, symbol):
        logger.critical(
            f"[POSITION] Capital deploy failed for {symbol} ₹{capital_deployed:,.2f} — "
            f"position NOT opened (trade={trade_id})"
        )
        return None

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO positions (
                    trade_id, order_id, symbol, action, quantity,
                    entry_price, stop_loss, target, capital_deployed, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                RETURNING position_id, opened_at
                """,
                (
                    trade_id, order_id, symbol, action, quantity,
                    filled_price, stop_loss, target, capital_deployed,
                ),
            )
            row = cur.fetchone()
            position_id = str(row[0])
            opened_at   = row[1].isoformat() if row[1] else None

        logger.info(
            f"[POSITION] Opened: {action} {quantity}x {symbol} @ {filled_price:.2f} "
            f"(position_id={position_id}, deployed=₹{capital_deployed:,.2f})"
        )
        return {
            "position_id":      position_id,
            "symbol":           symbol,
            "action":           action,
            "quantity":         quantity,
            "entry_price":      float(filled_price),
            "stop_loss":        float(stop_loss),
            "target":           float(target),
            "capital_deployed": capital_deployed,
            "status":           "open",
            "opened_at":        opened_at,
        }
    except Exception as e:
        logger.error(f"[POSITION] DB insert failed for {symbol}: {e}")
        # Attempt to roll back capital deployment
        release_capital(capital_deployed, 0.0)
        return None


def close_position(position_id: str, exit_price: float, exit_reason: str) -> Optional[dict]:
    """
    Close an open position: calculate P&L, update DB, release capital.
    Idempotent — returns existing closed position if already closed.
    """
    position = get_position(position_id)
    if position is None:
        logger.error(f"[POSITION] Cannot close — position {position_id} not found")
        return None

    if position.get("status") == "closed":
        logger.info(f"[POSITION] Position {position_id} already closed — returning existing")
        return position

    action           = position["action"]
    entry_price      = float(position["entry_price"])
    quantity         = int(position["quantity"])
    capital_deployed = float(position["capital_deployed"])
    symbol           = position["symbol"]

    if action == "BUY":
        realised_pnl = (exit_price - entry_price) * quantity
    else:
        realised_pnl = (entry_price - exit_price) * quantity

    now = datetime.now(timezone.utc)
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                UPDATE positions
                SET status='closed', exit_price=%s, exit_reason=%s,
                    realised_pnl=%s, closed_at=%s, updated_at=%s
                WHERE position_id=%s
                """,
                (exit_price, exit_reason, realised_pnl, now, now, position_id),
            )

        release_capital(capital_deployed, realised_pnl)
        reset_exposure(symbol)

        logger.info(
            f"[POSITION] Closed {position_id} ({symbol}) @ {exit_price:.2f} "
            f"reason={exit_reason} P&L={realised_pnl:+.2f}"
        )
        return {
            "position_id":  position_id,
            "symbol":       symbol,
            "exit_price":   exit_price,
            "exit_reason":  exit_reason,
            "realised_pnl": realised_pnl,
            "status":       "closed",
            "closed_at":    now.isoformat(),
        }
    except Exception as e:
        logger.error(f"[POSITION] close_position failed: {e}")
        return None


def get_open_positions() -> list:
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
            )
            rows = cur.fetchall()
            if not rows:
                return []
            cols = [d[0] for d in cur.description]
            return [_row_to_dict(cols, r) for r in rows]
    except Exception as e:
        logger.error(f"[POSITION] get_open_positions failed: {e}")
        return []


def get_position(position_id: str) -> Optional[dict]:
    try:
        with db_cursor() as cur:
            cur.execute("SELECT * FROM positions WHERE position_id = %s", (position_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return _row_to_dict(cols, row)
    except Exception as e:
        logger.error(f"[POSITION] get_position failed: {e}")
        return None


def get_position_by_trade_id(trade_id: str) -> Optional[dict]:
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT * FROM positions WHERE trade_id = %s ORDER BY opened_at DESC LIMIT 1",
                (trade_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return _row_to_dict(cols, row)
    except Exception as e:
        logger.error(f"[POSITION] get_position_by_trade_id failed: {e}")
        return None


def update_current_prices(price_map: dict) -> bool:
    """
    Bulk update current_price and unrealised_pnl for all open positions.
    price_map: {symbol: current_price}
    """
    if not price_map:
        return True
    try:
        for symbol, current_price in price_map.items():
            with db_cursor() as cur:
                cur.execute(
                    """
                    UPDATE positions
                    SET current_price = %s,
                        unrealised_pnl = CASE
                            WHEN action = 'BUY'  THEN (%s - entry_price) * quantity
                            WHEN action = 'SELL' THEN (entry_price - %s) * quantity
                        END,
                        updated_at = NOW()
                    WHERE symbol = %s AND status = 'open'
                    """,
                    (current_price, current_price, current_price, symbol),
                )
        return True
    except Exception as e:
        logger.error(f"[POSITION] update_current_prices failed: {e}")
        return False


def _row_to_dict(cols: list, row: tuple) -> dict:
    d = dict(zip(cols, row))
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "__float__"):
            try:
                d[k] = float(v)
            except Exception:
                pass
    for uuid_col in ("position_id", "trade_id"):
        if d.get(uuid_col):
            d[uuid_col] = str(d[uuid_col])
    return d
