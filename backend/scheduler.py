#!/usr/bin/env python3
"""
Scheduler for running the AI trading pipeline at configurable intervals.

Job schedule:
  Every 15 min  — exit_monitor.run_exit_checks() (runs BEFORE analysis)
  Every 15 min  — analysis pipeline per symbol
  Daily 15:30   — pnl_calculator.take_snapshot()

Kill switch blocks entry, NOT exit. Exit monitor always runs.
"""
import time
import schedule
from dotenv import load_dotenv
load_dotenv()

from main import run
from utils.market_hours import is_market_open
from portfolio.exit_monitor import run_exit_checks
from portfolio.pnl_calculator import take_snapshot
from safety.kill_switch import check_and_halt_if_degraded
from utils.logger import setup_logger
from config import Config

logger = setup_logger(__name__)


def exit_job():
    """Run exit checks for all open positions."""
    logger.info("[SCHEDULER] Running exit monitor")
    result = run_exit_checks()
    logger.info(
        f"[SCHEDULER] Exit monitor done: "
        f"checked={result['checked']} exited={result['exited']} errors={result['errors']}"
    )


def degradation_check_job():
    """Check model health; activate kill switch if degraded."""
    halted = check_and_halt_if_degraded()
    if halted:
        logger.critical(
            "[SCHEDULER] Model degradation detected — trading halted. "
            "Manual /resume required after investigation."
        )


def analysis_job(symbol):
    """Run the full analysis pipeline for a symbol."""
    if not is_market_open():
        logger.info(f"[SCHEDULER] Market closed — skipping analysis for {symbol}")
        return
    # Skip if kill switch is active (including auto-halt from degradation check)
    from safety.kill_switch import is_trading_halted
    if is_trading_halted():
        logger.warning(f"[SCHEDULER] Kill switch active — skipping analysis for {symbol}")
        return
    logger.info(f"[SCHEDULER] Starting analysis job for: {symbol}")
    result = run(symbol)
    logger.info(
        f"[SCHEDULER] Analysis done for {symbol}: "
        f"{result.get('decision', 'ERROR')}"
    )


def snapshot_job():
    """Take daily P&L snapshot at market close."""
    logger.info("[SCHEDULER] Taking daily P&L snapshot")
    ok = take_snapshot()
    logger.info(f"[SCHEDULER] Snapshot {'saved' if ok else 'FAILED'}")


def log_retention_job():
    """Delete aged rows from ephemeral log tables (runs at 02:00 IST)."""
    logger.info("[SCHEDULER] Running log retention")
    try:
        from maintenance.log_retention import run_retention
        result = run_retention()
        logger.info(
            f"[SCHEDULER] Log retention done — "
            f"audit_log={result['audit_log_deleted']} "
            f"pipeline_metrics={result['pipeline_metrics_deleted']} "
            f"llm_calls={result['llm_calls_deleted']} "
            f"rate_limit={result['rate_limit_deleted']}"
        )
    except Exception as e:
        logger.error(f"[SCHEDULER] log_retention_job error: {e}")


def pending_trade_evaluation_job():
    """
    Evaluate any trades that are still pending result by checking
    current price against their stop loss and target.
    Only runs during market hours.
    """
    if not is_market_open():
        return
    from memory.memory_store import get_all_trades
    from agents.feedback_agent import evaluate
    from broker.broker_factory import get_broker
    broker = get_broker()
    trades = get_all_trades()
    pending = [
        t for t in trades.values()
        if t.get("result") is None or t.get("result") == "pending"
    ]
    logger.info(f"[SCHEDULER] Evaluating {len(pending)} pending trades")
    for trade in pending:
        symbol   = trade.get("symbol")
        trade_id = trade.get("trade_id")
        ltp = broker.get_ltp(symbol)
        if ltp is None:
            continue
        evaluate(trade_id, ltp)


def db_cleanup_job():
    """ANALYZE tables and reset stale backtest runs (runs at 03:00 IST)."""
    logger.info("[SCHEDULER] Running DB cleanup")
    try:
        from maintenance.db_cleanup import run_all
        result = run_all()
        logger.info(
            f"[SCHEDULER] DB cleanup done — "
            f"vacuum_ok={result['vacuum_ok']} "
            f"stale_runs_reset={result['stale_runs_reset']} "
            f"idem_keys_purged={result['idempotency_keys_purged']}"
        )
    except Exception as e:
        logger.error(f"[SCHEDULER] db_cleanup_job error: {e}")


def get_watchlist_symbols():
    """
    Return active symbols from the DB watchlist.
    Falls back to Config.SYMBOLS if the watchlist table is empty or unavailable.
    """
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute(
                "SELECT symbol FROM watchlist WHERE is_active = TRUE ORDER BY added_at ASC"
            )
            rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"[SCHEDULER] Could not read watchlist from DB: {e}")
    fallback = getattr(Config, 'SYMBOLS', ['RELIANCE.NS'])
    logger.info(f"[SCHEDULER] Watchlist empty — falling back to Config.SYMBOLS: {fallback}")
    return fallback


def watchlist_analysis_job():
    """Run analysis for every active symbol in the watchlist."""
    symbols = get_watchlist_symbols()
    logger.info(f"[SCHEDULER] Running analysis for watchlist: {symbols}")
    for symbol in symbols:
        analysis_job(symbol)


def run_scheduler():
    """Set up and run the scheduler."""
    from config import validate_required_env
    validate_required_env()

    # Degradation check — runs every 15 min, BEFORE analysis
    schedule.every(15).minutes.do(degradation_check_job)

    # Exit monitor — runs every 15 min, BEFORE analysis
    schedule.every(15).minutes.do(exit_job)

    # Analysis pipeline — reads watchlist from DB each time, so adding/removing
    # symbols via the dashboard takes effect on the next cycle without a restart
    schedule.every(15).minutes.do(watchlist_analysis_job)
    logger.info("[SCHEDULER] Scheduled watchlist analysis every 15 minutes (dynamic)")

    # Pending trade evaluation — every 15 min during market hours
    schedule.every(15).minutes.do(pending_trade_evaluation_job)
    logger.info("[SCHEDULER] Scheduled pending trade evaluation every 15 minutes")

    # Daily P&L snapshot at market close (15:30 IST)
    schedule.every().day.at("15:30").do(snapshot_job)
    logger.info("[SCHEDULER] Scheduled daily P&L snapshot at 15:30 IST")

    # Maintenance jobs (IST times as UTC offset: IST = UTC+5:30)
    # 02:00 IST = 20:30 UTC previous day — use UTC times for schedule
    schedule.every().day.at("20:30").do(log_retention_job)
    logger.info("[SCHEDULER] Scheduled log retention at 02:00 IST (20:30 UTC)")

    # 03:00 IST = 21:30 UTC previous day
    schedule.every().day.at("21:30").do(db_cleanup_job)
    logger.info("[SCHEDULER] Scheduled DB cleanup at 03:00 IST (21:30 UTC)")

    # Run once immediately at startup
    degradation_check_job()
    exit_job()
    pending_trade_evaluation_job()
    watchlist_analysis_job()

    logger.info("[SCHEDULER] Scheduler running. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    run_scheduler()
