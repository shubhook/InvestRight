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

if not JWT_SECRET:
    raise EnvironmentError("JWT_SECRET environment variable is not set.")
if not API_KEY:
    raise EnvironmentError("API_KEY environment variable is not set.")

class Config:
    STOCK_API_KEY = os.getenv('API_KEY_STOCK')
    NEWS_API_KEY = os.getenv('API_KEY_NEWS')
    # Add other configurations as needed