# Stores API keys, configs, environment variables

import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

JWT_SECRET = os.getenv('JWT_SECRET')
JWT_EXPIRY_HOURS = int(os.getenv('JWT_EXPIRY_HOURS', 24))
API_KEY = os.getenv('API_KEY')
DEFAULT_CAPITAL_LIMIT = float(os.getenv('DEFAULT_CAPITAL_LIMIT', 10.0))

BROKER_MODE    = os.getenv('BROKER_MODE', 'paper')
TOTAL_CAPITAL  = float(os.getenv('TOTAL_CAPITAL', 0))
KITE_API_KEY    = os.getenv('KITE_API_KEY')
KITE_API_SECRET = os.getenv('KITE_API_SECRET')
KITE_ACCESS_TOKEN = os.getenv('KITE_ACCESS_TOKEN')
KITE_PRODUCT    = os.getenv('KITE_PRODUCT', 'MIS')

BACKTEST_DEFAULT_CAPITAL = float(os.getenv('BACKTEST_DEFAULT_CAPITAL', 100000.0))
BACKTEST_MAX_WORKERS     = int(os.getenv('BACKTEST_MAX_WORKERS', 4))

def validate_required_env():
    """
    Call this from main.py and scheduler.py at startup — not at import time.
    Raises EnvironmentError with a clear message if required vars are missing.
    """
    missing = []
    if not JWT_SECRET:
        missing.append("JWT_SECRET")
    if not API_KEY:
        missing.append("API_KEY")
    if not TOTAL_CAPITAL:
        missing.append("TOTAL_CAPITAL")
    if missing:
        raise EnvironmentError(
            f"Required environment variables not set: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in the values."
        )


# --- Technical indicator parameters ---
ATR_PERIOD               = int(os.getenv("ATR_PERIOD",              14))
SMA_FAST                 = int(os.getenv("SMA_FAST",                20))
SMA_SLOW                 = int(os.getenv("SMA_SLOW",                50))
RSI_PERIOD               = int(os.getenv("RSI_PERIOD",              14))
MACD_FAST                = int(os.getenv("MACD_FAST",               12))
MACD_SLOW                = int(os.getenv("MACD_SLOW",               26))
MACD_SIGNAL              = int(os.getenv("MACD_SIGNAL",              9))
PATTERN_CONFIDENCE_FLOOR = float(os.getenv("PATTERN_CONFIDENCE_FLOOR", 0.5))
MAX_KELLY_FRACTION       = float(os.getenv("MAX_KELLY_FRACTION",    0.50))
MAX_LOSS_HARD_CAP        = float(os.getenv("MAX_LOSS_HARD_CAP",     0.10))
SR_WINDOW                = int(os.getenv("SR_WINDOW",               10))
MIN_CANDLES              = int(os.getenv("MIN_CANDLES",             30))


class Config:
    STOCK_API_KEY = os.getenv('API_KEY_STOCK')
    NEWS_API_KEY = os.getenv('API_KEY_NEWS')
    # Add other configurations as needed