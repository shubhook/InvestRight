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
from portfolio.exit_monitor import run_exit_checks
from portfolio.pnl_calculator import take_snapshot
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


def analysis_job(symbol):
    """Run the full analysis pipeline for a symbol."""
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


def run_scheduler():
    """Set up and run the scheduler."""
    symbols = getattr(Config, 'SYMBOLS', ['RELIANCE.NS'])

    # Exit monitor — runs every 15 min, BEFORE analysis
    schedule.every(15).minutes.do(exit_job)

    # Analysis pipeline — per symbol, every 15 min
    for symbol in symbols:
        schedule.every(15).minutes.do(analysis_job, symbol)
        logger.info(f"[SCHEDULER] Scheduled analysis for {symbol} every 15 minutes")

    # Daily P&L snapshot at market close (15:30 IST)
    schedule.every().day.at("15:30").do(snapshot_job)
    logger.info("[SCHEDULER] Scheduled daily P&L snapshot at 15:30 IST")

    # Run once immediately at startup
    exit_job()
    for symbol in symbols:
        analysis_job(symbol)

    logger.info("[SCHEDULER] Scheduler running. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    run_scheduler()
