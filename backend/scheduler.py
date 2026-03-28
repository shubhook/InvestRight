#!/usr/bin/env python3
"""
Scheduler for running the AI trading pipeline at configurable intervals.
"""
import time
import schedule
from main import run
from utils.logger import setup_logger
from config import Config

logger = setup_logger(__name__)

def job(symbol):
    """Job to run the pipeline for a given symbol."""
    logger.info(f"[SCHEDULER] Starting job for symbol: {symbol}")
    result = run(symbol)
    logger.info(f"[SCHEDULER] Job completed for {symbol}: {result.get('decision', {}).get('action', 'ERROR')}")

def run_scheduler():
    """Set up and run the scheduler."""
    # Read symbols from config or use default
    symbols = getattr(Config, 'SYMBOLS', ['RELIANCE.NS'])  # Default symbol
    
    # Schedule jobs for each symbol
    for symbol in symbols:
        # Run every 15 minutes
        schedule.every(15).minutes.do(job, symbol)
        logger.info(f"[SCHEDULER] Scheduled job for {symbol} every 15 minutes")
    
    # Run once immediately at start
    for symbol in symbols:
        job(symbol)
    
    logger.info("[SCHEDULER] Scheduler started. Press Ctrl+C to exit.")
    
    # Keep the scheduler running
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    run_scheduler()