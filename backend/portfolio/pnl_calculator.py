from datetime import datetime, timezone, date
from typing import Optional
from db.connection import db_cursor
from portfolio.capital_account import get_account
from utils.logger import setup_logger

logger = setup_logger(__name__)


def get_portfolio_summary() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    try:
        acct = get_account() or {
            "total_capital": 0.0, "deployed_capital": 0.0,
            "available_capital": 0.0, "realised_pnl": 0.0,
        }

        with db_cursor() as cur:
            # Unrealised P&L from open positions
            cur.execute(
                "SELECT COALESCE(SUM(unrealised_pnl), 0) FROM positions WHERE status='open'"
            )
            unrealised = float(cur.fetchone()[0] or 0.0)

            # Position counts
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='open')   AS open_count,
                    COUNT(*) FILTER (WHERE status='closed') AS closed_count,
                    COUNT(*)                                 AS total_count
                FROM positions
                """
            )
            row = cur.fetchone()
            pos_open, pos_closed, pos_total = int(row[0]), int(row[1]), int(row[2])

            # Trade stats from trades table
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE result='correct') AS wins,
                    COUNT(*) FILTER (WHERE result='wrong')   AS losses,
                    COUNT(*) FILTER (WHERE result IS NULL OR result='pending') AS pending
                FROM trades
                """
            )
            row2 = cur.fetchone()
            wins, losses, pending = int(row2[0]), int(row2[1]), int(row2[2])

        total_capital = acct["total_capital"]
        realised      = acct["realised_pnl"]
        total_pnl     = realised + unrealised
        evaluated     = wins + losses

        return {
            "capital": {
                "total":      total_capital,
                "deployed":   acct["deployed_capital"],
                "available":  acct["available_capital"],
                "return_pct": round(total_pnl / total_capital * 100, 4) if total_capital else 0.0,
            },
            "pnl": {
                "unrealised": round(unrealised, 2),
                "realised":   round(realised, 2),
                "total":      round(total_pnl, 2),
                "total_pct":  round(total_pnl / total_capital * 100, 4) if total_capital else 0.0,
            },
            "positions": {
                "open":   pos_open,
                "closed": pos_closed,
                "total":  pos_total,
            },
            "trades": {
                "wins":     wins,
                "losses":   losses,
                "pending":  pending,
                "win_rate": round(wins / evaluated, 4) if evaluated else 0.0,
            },
            "generated_at": now,
        }
    except Exception as e:
        logger.error(f"[PNL] get_portfolio_summary failed: {e}")
        return {
            "capital":   {"total": 0.0, "deployed": 0.0, "available": 0.0, "return_pct": 0.0},
            "pnl":       {"unrealised": 0.0, "realised": 0.0, "total": 0.0, "total_pct": 0.0},
            "positions": {"open": 0, "closed": 0, "total": 0},
            "trades":    {"wins": 0, "losses": 0, "pending": 0, "win_rate": 0.0},
            "generated_at": now,
        }


def get_position_pnl(position_id: str) -> Optional[dict]:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT position_id, symbol, action, entry_price, current_price,
                       exit_price, quantity, unrealised_pnl, realised_pnl, status
                FROM positions WHERE position_id = %s
                """,
                (position_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None

        (pid, symbol, action, entry, current, exit_p, qty,
         unrealised, realised, status) = row

        entry   = float(entry)
        qty     = int(qty)
        ref_price = float(exit_p) if exit_p else float(current) if current else entry

        if action == "BUY":
            pnl_pct = (ref_price - entry) / entry * 100 if entry else 0.0
        else:
            pnl_pct = (entry - ref_price) / entry * 100 if entry else 0.0

        return {
            "position_id":    str(pid),
            "symbol":         symbol,
            "action":         action,
            "entry_price":    entry,
            "current_price":  float(current) if current else None,
            "exit_price":     float(exit_p) if exit_p else None,
            "quantity":       qty,
            "unrealised_pnl": float(unrealised) if unrealised is not None else None,
            "realised_pnl":   float(realised) if realised is not None else None,
            "return_pct":     round(pnl_pct, 4),
            "status":         status,
        }
    except Exception as e:
        logger.error(f"[PNL] get_position_pnl failed: {e}")
        return None


def get_symbol_pnl(symbol: str) -> dict:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(realised_pnl), 0)   AS total_realised,
                    COALESCE(SUM(unrealised_pnl), 0) AS total_unrealised,
                    COUNT(*)                          AS total_positions,
                    COUNT(*) FILTER (WHERE status='open') AS open_positions
                FROM positions WHERE symbol = %s
                """,
                (symbol,),
            )
            row = cur.fetchone()
        return {
            "symbol":            symbol,
            "realised_pnl":      float(row[0]),
            "unrealised_pnl":    float(row[1]),
            "total_positions":   int(row[2]),
            "open_positions":    int(row[3]),
        }
    except Exception as e:
        logger.error(f"[PNL] get_symbol_pnl failed: {e}")
        return {"symbol": symbol, "realised_pnl": 0.0, "unrealised_pnl": 0.0,
                "total_positions": 0, "open_positions": 0}


def get_daily_pnl() -> dict:
    today = date.today()
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*)                                  AS total,
                    COUNT(*) FILTER (WHERE t.result='correct') AS wins,
                    COUNT(*) FILTER (WHERE t.result='wrong')   AS losses,
                    COALESCE(SUM(p.realised_pnl), 0)          AS realised_pnl,
                    COALESCE(SUM(p.unrealised_pnl), 0)        AS unrealised_pnl
                FROM positions p
                JOIN trades t ON p.trade_id = t.trade_id
                WHERE DATE(p.opened_at AT TIME ZONE 'Asia/Kolkata') = %s
                """,
                (today,),
            )
            row = cur.fetchone()
        total, wins, losses, realised, unrealised = (
            int(row[0]), int(row[1]), int(row[2]), float(row[3]), float(row[4])
        )
        return {
            "date":          today.isoformat(),
            "total_trades":  total,
            "wins":          wins,
            "losses":        losses,
            "realised_pnl":  round(realised, 2),
            "unrealised_pnl": round(unrealised, 2),
            "net_pnl":       round(realised + unrealised, 2),
        }
    except Exception as e:
        logger.error(f"[PNL] get_daily_pnl failed: {e}")
        return {"date": today.isoformat(), "total_trades": 0, "wins": 0,
                "losses": 0, "realised_pnl": 0.0, "unrealised_pnl": 0.0, "net_pnl": 0.0}


def take_snapshot() -> bool:
    """
    Write today's portfolio state to pnl_snapshots.
    Idempotent — upserts on snapshot_date.
    """
    try:
        summary = get_portfolio_summary()
        today   = date.today()

        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO pnl_snapshots (
                    snapshot_date, total_capital, deployed_capital,
                    available_capital, unrealised_pnl, realised_pnl, open_positions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_date) DO UPDATE SET
                    total_capital     = EXCLUDED.total_capital,
                    deployed_capital  = EXCLUDED.deployed_capital,
                    available_capital = EXCLUDED.available_capital,
                    unrealised_pnl    = EXCLUDED.unrealised_pnl,
                    realised_pnl      = EXCLUDED.realised_pnl,
                    open_positions    = EXCLUDED.open_positions
                """,
                (
                    today,
                    summary["capital"]["total"],
                    summary["capital"]["deployed"],
                    summary["capital"]["available"],
                    summary["pnl"]["unrealised"],
                    summary["pnl"]["realised"],
                    summary["positions"]["open"],
                ),
            )
        logger.info(f"[PNL] Snapshot taken for {today}")
        return True
    except Exception as e:
        logger.error(f"[PNL] take_snapshot failed: {e}")
        return False
