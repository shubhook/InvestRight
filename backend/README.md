# AI Trading System - Chart Pattern Detection (MVP)

A rule-based, explainable AI trading system that detects chart patterns, applies strict risk management, and learns from outcomes.

## Overview

This system implements a deterministic trading pipeline:
```
Data → Analysis → Pattern Detection → Decision → Risk Management → Action → Feedback → Memory
```

## Key Features

- **Rule-Based Only**: Zero machine learning models - all logic is transparent and explainable
- **Pattern Recognition**: Detects Double Top, Ascending Triangle, and Head & Shoulders patterns
- **Risk Management**: Enforces 2% max loss per trade and minimum 2:1 reward-to-risk ratio
- **News Sentiment**: Uses keyword-based analysis as a secondary signal
- **Feedback Loop**: Learns from trade outcomes to improve future decisions
- **REST API**: Provides analysis via HTTP endpoint
- **Scheduler**: Automated analysis at configurable intervals

## Components

### Backend (`backend/`)
- **Agents**: Data, Analysis, Decision, Action, Feedback
- **Services**: Stock data (yfinance/Alpha Vantage) and News (RSS) fetchers
- **Utils**: Pattern detection, risk management, logging, helpers
- **Memory**: JSON-based storage for trades and outcomes
- **API**: Flask REST endpoint for analysis
- **Scheduler**: Automated pipeline execution

### Frontend (`frontend/`)
- Basic HTML/CSS/JS interface for manual interaction

## Getting Started

### Prerequisites
- Python 3.8+
- pip

### Installation
1. Clone the repository
2. Create a `.env` file with your API keys:
   ```
   API_KEY_STOCK=your_alpha_vantage_key_here
   API_KEY_NEWS=your_newsapi_key_here
   ```
3. Install dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

### Running the System

#### Option 1: Manual Analysis (API)
```bash
# Start the API server
python backend/main.py

# In another terminal, analyze a symbol
curl "http://localhost:5001/analyze?symbol=RELIANCE.NS"
```

#### Option 2: Automated Scheduler
```bash
# Start the scheduler (runs every 15 minutes)
python backend/scheduler.py
```

#### Option 3: Direct Pipeline Execution
```bash
# Run analysis for a single symbol
python -c "
from backend.main import run
result = run('RELIANCE.NS')
print(result['decision'])
"
```

## Project Structure
```
backend/
├── agents/
│   ├── data_agent.py          # Fetch OHLCV + News
│   ├── analysis_agent.py      # Trend, S/R, volatility, sentiment
│   ├── decision_agent.py      # Pattern + trend → BUY/SELL/WAIT
│   ├── action_agent.py        # Execute/store trades
│   └── feedback_agent.py      # Evaluate outcomes
│
├── services/
│   ├── stock_service.py       # yfinance primary, Alpha Vantage fallback
│   └── news_service.py        # Google Finance RSS
│
├── utils/
│   ├── pattern_engine.py      # Double Top, Asc Triangle, H&S detection
│   ├── risk_engine.py         # Stop loss, target, 2:1 RR, max 2% loss
│   ├── logger.py              # Structured logging
│   └── helpers.py             # Common utilities
│
├── memory/
│   ├── memory_store.py        # Store trade records
│   └── memory_reader.py       # Retrieve trades, calc success rates
│
├── config.py                  # Environment variables
├── main.py                    # API endpoint + pipeline orchestrator
├── scheduler.py               # Automated execution every 15 min
└── requirements.txt           # Python dependencies

frontend/
├── index.html                 # Basic UI
├── script.js                  # API interaction
└── style.css                  # Styling

database/
└── schema.json                # Placeholder for DB schema

logs/
└── system.log                 # Runtime logs

docs/
├── agents.md                  # Detailed agent specifications
└── system_design.md           # First principles, pipeline reasoning
```

## How It Works

### Data Collection
- Fetches 1-hour OHLCV data for the last month (yfinance primary)
- Retrieves latest news headlines via Google Finance RSS
- Packages data for pipeline processing

### Analysis
- **Trend**: SMA-20 > SMA-50 = uptrend, else downtrend
- **Support/Resistance**: Local minima/maxima using rolling windows
- **Volatility**: ATR (Average True Range) over 14 periods
- **Sentiment**: Keyword scoring (positive/negative word lists)

### Pattern Detection
1. **Double Top** (Bearish): Two similar peaks with valley between
2. **Ascending Triangle** (Bullish): Flat resistance + rising support
3. **Head & Shoulders** (Bearish): Three peaks with middle highest

Each pattern returns confidence (0.0-1.0) and direction.

### Decision Logic
- IF pattern confidence < 0.6 → WAIT
- IF pattern bullish AND trend up → BUY
- IF pattern bearish AND trend down → SELL
- ELSE → WAIT
- Sentiment modifies confidence but doesn't override primary signal

### Risk Management (Non-Negotiable)
- Stop loss: Nearest support (BUY) or resistance (SELL)
- Target: Entry ± 2×(Entry-Stop Loss) for 2:1 reward-to-risk
- Reject if reward/risk < 2.0
- Reject if stop loss implies >2% loss from entry

### Execution & Feedback
- Trades are logged with ID, timestamp, entry, SL, target
- Feedback agent checks if price hit target or stop loss
- Results stored in memory for pattern success rate calculation

## API Endpoint

```
GET /analyze?symbol=RELIANCE.NS
```

### Response Format
```json
{
  "symbol": "RELIANCE.NS",
  "decision": "BUY",
  "confidence": 0.82,
  "reason": "Pattern: ascending_triangle, Trend: uptrend",
  "risk": {
    "entry": 2450.0,
    "stop_loss": 2390.0,
    "target": 2570.0,
    "rr_ratio": 2.0,
    "max_loss_pct": 2.45
  },
  "sentiment_flag": "positive",
  "analysis_summary": {
    "trend": "uptrend",
    "support_count": 3,
    "resistance_count": 2,
    "volatility": 1.25,
    "sentiment": "positive"
  },
  "pattern_detected": {
    "pattern": "ascending_triangle",
    "confidence": 0.81,
    "direction": "bullish"
  }
}
```

## Design Principles

1. **Explainability**: Every decision includes clear reasoning
2. **Risk First**: Capital preservation over profit maximization
3. **Modularity**: Each component has single responsibility
4. **Deterministic**: Same inputs → same outputs
5. **Feedback-Driven**: System learns from outcomes
6. **Extensible**: Easy to add patterns/signals/risk rules

## Extending the System

### Adding New Patterns
1. Create detection function in `backend/utils/pattern_engine.py`
2. Add to the `patterns` list in `detect_pattern()` function
3. Ensure function returns `{pattern: str, confidence: float}`

### Adding New Signals
1. Modify `backend/agents/analysis_agent.py`
2. Add calculations in `analyze_data()` function
3. Update return dictionary with new signal

### Adjusting Risk Rules
1. Modify `backend/utils/risk_engine.py`
2. Adjust stop loss/target logic or add new validation rules

## Configuration

Set these in `.env` or environment variables:
- `API_KEY_STOCK`: Alpha Vantage API key (yfinance fallback available)
- `API_KEY_NEWS`: NewsAPI key (Google Finance RSS used as fallback)
- `SYMBOLS`: Comma-separated list for scheduler (default: RELIANCE.NS)

## Testing

Run unit tests for core components:
```bash
# Pattern detection tests
python -m pytest tests/test_pattern_engine.py -v

# Risk engine tests
python -m pytest tests/test_risk_engine.py -v

# Decision logic tests
python -m pytest tests/test_decision_agent.py -v
```

## License

MIT