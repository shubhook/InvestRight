import requests
import feedparser
from urllib.error import URLError
from utils.logger import setup_logger

logger = setup_logger(__name__)

def fetch_news(symbol: str) -> list:
    """
    Fetch news for a given symbol using Google Finance RSS as primary source
    
    Args:
        symbol (str): Stock symbol (e.g., 'RELIANCE')
        
    Returns:
        List[str]: List of headline strings
    """
    try:
        # Clean symbol for RSS (remove .NS, .BO suffixes if present)
        clean_symbol = symbol.replace('.NS', '').replace('.BO', '')
        
        # Google Finance RSS URL
        rss_url = f"https://news.google.com/rss/search?q={clean_symbol}+stock"
        
        logger.info(f"[NEWS_SERVICE] Fetching news for {symbol} from Google Finance RSS")
        
        # Parse RSS feed
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            logger.warning(f"[NEWS_SERVICE] No news entries found for {symbol}")
            return []
            
        # Extract headlines
        headlines = [entry.title for entry in feed.entries[:10]]  # Limit to 10 most recent
        
        logger.info(f"[NEWS_SERVICE] Fetched {len(headlines)} headlines for {symbol}")
        return headlines
        
    except (URLError, ConnectionError, TimeoutError) as e:
        # Network-level failure — caller should distinguish this from "no news found"
        logger.error(f"[NEWS_SERVICE] Network error fetching news for {symbol}: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"[NEWS_SERVICE] Unexpected error fetching news for {symbol}: {str(e)}")
        return []

def fetch_news_with_retry(symbol: str, max_retries=2) -> list:
    """
    Fetch news with retry logic
    
    Args:
        symbol (str): Stock symbol
        max_retries (int): Maximum retry attempts
        
    Returns:
        List[str]: List of headline strings
    """
    for attempt in range(max_retries):
        headlines = fetch_news(symbol)
        if headlines:  # Return if we got any news
            return headlines
        logger.warning(f"[NEWS_SERVICE] Attempt {attempt + 1}/{max_retries} failed for {symbol}")
        if attempt < max_retries - 1:
            import time
            time.sleep(1)  # Brief pause before retry
    
    logger.error(f"[NEWS_SERVICE] All {max_retries} attempts failed for {symbol}")
    return []