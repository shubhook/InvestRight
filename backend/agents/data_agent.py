from services.stock_service import fetch_stock_data_reliable
from services.news_service import fetch_news_with_retry
from utils.logger import setup_logger
import pandas as pd

logger = setup_logger(__name__)

def fetch_and_package_data(symbol: str) -> dict:
    """
    Fetch and package data for the AI system pipeline.
    
    Args:
        symbol (str): Stock symbol (e.g., 'RELIANCE.NS')
        
    Returns:
        dict: {
            "symbol": str,
            "ohlc": pd.DataFrame,
            "volume": pd.Series,
            "news": List[str]
        }
        None: If data fetching fails
    """
    try:
        logger.info(f"[DATA_AGENT] Fetching data for {symbol}")
        
        # Fetch stock data
        ohlc_df = fetch_stock_data_reliable(symbol, interval="1h", period="1mo")
        
        if ohlc_df is None or ohlc_df.empty:
            logger.error(f"[DATA_AGENT] Failed to fetch stock data for {symbol} — symbol may be invalid or delisted")
            return None

        if len(ohlc_df) < 30:
            logger.warning(f"[DATA_AGENT] Only {len(ohlc_df)} candles returned for {symbol} — pattern detection requires 30+. Results may be limited.")
        
        # Ensure we have the required columns
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in ohlc_df.columns for col in required_columns):
            logger.error(f"[DATA_AGENT] Missing required columns in stock data for {symbol}")
            return None
        
        # Fetch news data
        news_list = fetch_news_with_retry(symbol)
        
        # Package the data
        packaged_data = {
            "symbol": symbol,
            "ohlc": ohlc_df[['open', 'high', 'low', 'close']],  # OHLC only
            "volume": ohlc_df['volume'],  # Volume series
            "news": news_list
        }
        
        logger.info(f"[DATA_AGENT] Successfully packaged data for {symbol}: "
                   f"{len(ohlc_df)} candles, {len(news_list)} news items")
        
        return packaged_data
        
    except Exception as e:
        logger.error(f"[DATA_AGENT] Error fetching and packaging data for {symbol}: {str(e)}")
        return None