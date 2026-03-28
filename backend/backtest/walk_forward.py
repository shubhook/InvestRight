"""
Walk-forward validation — splits data into n non-overlapping folds and runs
a backtest on the test portion of each fold.
"""
import uuid
from typing import Optional

import pandas as pd

from backtest.data_loader import split_into_windows
from backtest.backtest_engine import run_backtest
from backtest.performance import compute_metrics
from db.connection import db_cursor
from utils.logger import setup_logger

logger = setup_logger(__name__)


def run_walk_forward(
    symbol: str,
    ohlc_df: pd.DataFrame,
    initial_capital: float,
    n_splits: int = 5,
    train_pct: float = 0.7,
    weights: Optional[dict] = None,
) -> dict:
    """
    Walk-forward validation over n_splits non-overlapping folds.

    For each fold:
      1. Slice fold_df = ohlc_df[start:end]
      2. Split into (train_df, test_df) using train_pct
      3. Run backtest on test_df (train_df provides context for indicators)

    Args:
        symbol:          Stock symbol.
        ohlc_df:         Full historical OHLCV DataFrame.
        initial_capital: Starting capital per fold.
        n_splits:        Number of folds.
        train_pct:       Fraction of each fold kept as training context.
        weights:         Optional weight override.

    Returns:
        dict with 'folds' list and 'aggregate_metrics'.
    """
    if ohlc_df is None or ohlc_df.empty:
        return {"error": "Empty OHLCV data", "folds": [], "aggregate_metrics": {}}

    total_bars = len(ohlc_df)
    fold_size  = total_bars // n_splits

    if fold_size < 120:
        logger.warning(
            f"[WALK_FORWARD] Fold size {fold_size} bars is small — "
            "results may be unreliable"
        )

    folds           = []
    all_fold_trades = []

    for fold_idx in range(n_splits):
        start   = fold_idx * fold_size
        end     = start + fold_size if fold_idx < n_splits - 1 else total_bars
        fold_df = ohlc_df.iloc[start:end].copy()

        if len(fold_df) < 30:
            logger.warning(f"[WALK_FORWARD] Fold {fold_idx + 1} too small ({len(fold_df)} bars) — skipping")
            continue

        _, test_df = split_into_windows(fold_df, train_pct=train_pct)

        fold_run_id = str(uuid.uuid4())
        start_str   = str(test_df.index[0].date())  if len(test_df) > 0 else ""
        end_str     = str(test_df.index[-1].date()) if len(test_df) > 0 else ""

        _create_run_row(fold_run_id, symbol, start_str, end_str, initial_capital, "walk_forward")

        logger.info(
            f"[WALK_FORWARD] Fold {fold_idx + 1}/{n_splits}: "
            f"fold_bars={len(fold_df)} test_bars={len(test_df)} run_id={fold_run_id}"
        )

        result = run_backtest(
            run_id=fold_run_id,
            symbol=symbol,
            ohlc_df=test_df,
            initial_capital=initial_capital,
            weights=weights,
        )

        folds.append({
            "fold":    fold_idx + 1,
            "run_id":  fold_run_id,
            "bars":    len(test_df),
            "metrics": result.get("metrics", {}),
            "error":   result.get("error"),
        })
        all_fold_trades.extend(result.get("trades", []))

    aggregate = compute_metrics(all_fold_trades, initial_capital * n_splits)
    aggregate["folds_run"] = len(folds)

    logger.info(
        f"[WALK_FORWARD] Completed {len(folds)}/{n_splits} folds — "
        f"total_trades={aggregate['total_trades']}, "
        f"win_rate={aggregate['win_rate']:.2%}, "
        f"sharpe={aggregate['sharpe_ratio']}"
    )

    return {"folds": folds, "aggregate_metrics": aggregate}


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _create_run_row(
    run_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    interval: str,
) -> None:
    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs
                    (run_id, symbol, start_date, end_date, interval, initial_capital, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'running')
                """,
                (run_id, symbol, start_date, end_date, interval, initial_capital),
            )
    except Exception as e:
        logger.error(f"[WALK_FORWARD] _create_run_row error: {e}")
