"""
Historical data loader for backtesting.
Uses yfinance — same source as the live data_agent.
"""
from typing import Optional, Tuple

import pandas as pd
import yfinance as yf

from utils.logger import setup_logger

logger = setup_logger(__name__)


def load_historical_data(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "15m",
) -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data from yfinance.

    Args:
        symbol:     Stock symbol (e.g. "RELIANCE.NS")
        start_date: "YYYY-MM-DD"
        end_date:   "YYYY-MM-DD"
        interval:   yfinance interval string (e.g. "15m", "1h", "1d")

    Returns:
        DataFrame with lowercase columns [open, high, low, close, volume]
        indexed by datetime, or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date, end=end_date, interval=interval)

        if df is None or df.empty:
            logger.error(
                f"[DATA_LOADER] No data returned for {symbol} "
                f"({start_date} → {end_date}, interval={interval})"
            )
            return None

        df.columns = [c.lower() for c in df.columns]
        required = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.error(f"[DATA_LOADER] Missing columns for {symbol}: {missing}")
            return None

        df = df[required].copy()
        df.dropna(subset=["close"], inplace=True)

        if len(df) < 60:
            logger.warning(
                f"[DATA_LOADER] Only {len(df)} bars for {symbol} — "
                "results may be unreliable with short history"
            )

        logger.info(
            f"[DATA_LOADER] Loaded {len(df)} bars for {symbol} "
            f"({start_date} → {end_date}, interval={interval})"
        )
        return df

    except Exception as e:
        logger.error(f"[DATA_LOADER] Error loading {symbol}: {e}")
        return None


def split_into_windows(
    df: pd.DataFrame,
    train_pct: float = 0.7,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split a DataFrame into a train window and a test window.

    Args:
        df:         Full OHLCV DataFrame
        train_pct:  Fraction of rows for training (default 0.7)

    Returns:
        (train_df, test_df) — non-overlapping, in-order slices.
    """
    if df is None or df.empty:
        raise ValueError("DataFrame is empty or None")

    split_idx = int(len(df) * train_pct)
    train_df = df.iloc[:split_idx].copy()
    test_df  = df.iloc[split_idx:].copy()

    logger.info(
        f"[DATA_LOADER] Split: {len(train_df)} train / {len(test_df)} test bars "
        f"(train_pct={train_pct:.0%})"
    )
    return train_df, test_df
