# Stores API keys, configs, environment variables

import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

class Config:
    STOCK_API_KEY = os.getenv('API_KEY_STOCK')
    NEWS_API_KEY = os.getenv('API_KEY_NEWS')
    # Add other configurations as needed