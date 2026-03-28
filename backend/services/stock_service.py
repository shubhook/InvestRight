import yfinance as yf
import pandas as pd
import requests
import time
from utils.logger import setup_logger

logger = setup_logger(__name__)

def fetch_stock_data(symbol: str, interval="1h", period="1mo"):
    """
    Fetch OHLCV stock data with yfinance primary and Alpha Vantage fallback
    
    Args:
        symbol (str): Stock symbol (e.g., 'RELIANCE.NS')
        interval (str): Data interval (default: '1h')
        period (str): Data period (default: '1mo')
        
    Returns:
        pd.DataFrame: DataFrame with columns [open, high, low, close, volume]
        None: If data fetch fails
    """
    try:
        # Primary: yfinance
        logger.info(f"[STOCK_SERVICE] Fetching data for {symbol} via yfinance")
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=interval, period=period)
        
        if df.empty or len(df) < 10:
            raise ValueError("Insufficient data from yfinance")
            
        # Standardize column names
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high', 
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })
        
        logger.info(f"[STOCK_SERVICE] Successfully fetched {len(df)} candles for {symbol}")
        return df[['open', 'high', 'low', 'close', 'volume']]
        
    except Exception as e:
        logger.warning(f"[STOCK_SERVICE] yfinance failed for {symbol}: {str(e)}")
        # Fallback: Alpha Vantage (simplified - would need API key in practice)
        try:
            logger.info(f"[STOCK_SERVICE] Trying Alpha Vantage fallback for {symbol}")
            # Note: In production, you would use actual Alpha Vantage API with key
            # For MVP, we'll return None to trigger proper error handling
            logger.error(f"[STOCK_SERVICE] Alpha Vantage fallback not implemented in MVP")
            return None
        except Exception as fallback_error:
            logger.error(f"[STOCK_SERVICE] Both yfinance and Alpha Vantage failed for {symbol}: {str(fallback_error)}")
            return None

def fetch_stock_data_reliable(symbol: str, interval="1h", period="1mo", max_retries=3):
    """
    Fetch stock data with retry logic
    
    Args:
        symbol (str): Stock symbol
        interval (str): Data interval
        period (str): Data period
        max_retries (int): Maximum retry attempts
        
    Returns:
        pd.DataFrame: Stock data or None if all attempts fail
    """
    for attempt in range(max_retries):
        data = fetch_stock_data(symbol, interval, period)
        if data is not None and not data.empty:
            return data
        logger.warning(f"[STOCK_SERVICE] Attempt {attempt + 1}/{max_retries} failed for {symbol}")
        if attempt < max_retries - 1:
            time.sleep(1)  # Brief pause before retry
    
    logger.error(f"[STOCK_SERVICE] All {max_retries} attempts failed for {symbol}")
    return None