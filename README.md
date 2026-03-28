# AI Multi-Agent System

This project implements a multi-agent AI system for data collection, analysis, decision making, action execution, and feedback learning.

## Structure

- `backend/`: Contains the core AI system with agents, services, memory, utils, and models.
- `frontend/`: Simple web interface for interacting with the system.
- `database/`: Optional database schema.
- `logs/`: Runtime logs.
- `.env`: Environment variables for API keys.
- `run.sh`: Script to start the system.

## Agents

1. Data Agent: Collects raw data from APIs and web scraping.
2. Analysis Agent: Processes and analyzes the collected data.
3. Decision Agent: Makes decisions based on the analysis.
4. Action Agent: Executes the decisions (e.g., trades, alerts).
5. Feedback Agent: Learns from the results and improves the system.

## Running Locally (Step-by-Step)

### Prerequisites

- Python 3.9+
- `pip`
- macOS / Linux (or WSL on Windows)

---

### Step 1 — Clone and enter the project

```bash
cd /path/to/InvestRight/project
```

---

### Step 2 — Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> On Windows: `.venv\Scripts\activate`

---

### Step 3 — Install dependencies

```bash
pip install -r backend/requirements.txt
```

---

### Step 4 — Configure environment variables (optional)

The system works out of the box without API keys — it uses `yfinance` for stock data and Google Finance RSS for news, both of which are free and keyless.

If you have API keys, copy the `.env` file and fill them in:

```bash
# .env
API_KEY_STOCK=your_key_here
API_KEY_NEWS=your_key_here
```

To enable Flask debug mode during development:

```bash
FLASK_DEBUG=true   # add to .env
```

---

### Step 5 — Start the backend (Flask API)

```bash
cd backend
python main.py
```

The API will be available at: **http://localhost:5001**

---

### Step 6 — Start the frontend (in a new terminal)

```bash
cd frontend
python -m http.server 8080
```

The UI will be available at: **http://localhost:8080**

---

### Step 7 — Test the API

Open your browser to **http://localhost:8080**, type a stock symbol (e.g. `ADANIENT.NS`), and click **Analyze**.

Or use `curl` directly:

```bash
# NSE stocks — append .NS
curl "http://localhost:5001/analyze?symbol=ADANIENT.NS"
curl "http://localhost:5001/analyze?symbol=ADANIPORTS.NS"
curl "http://localhost:5001/analyze?symbol=RELIANCE.NS"

# BSE stocks — append .BO
curl "http://localhost:5001/analyze?symbol=ADANIENT.BO"
```

Example response:

```json
{
  "symbol": "ADANIENT.NS",
  "decision": "WAIT",
  "confidence": 0.0,
  "reason": "Pattern confidence too low",
  "risk": {
    "entry": null,
    "stop_loss": null,
    "target": null,
    "rr_ratio": null,
    "max_loss_pct": null
  },
  "analysis_summary": {
    "trend": "downtrend",
    "support_count": 5,
    "resistance_count": 5,
    "volatility": 22.0,
    "sentiment": "neutral"
  },
  "pattern_detected": {
    "pattern": "none",
    "confidence": 0.0,
    "direction": "neutral"
  }
}
```

---

### Step 8 — Run the scheduler (optional)

The scheduler runs the full pipeline automatically every 15 minutes for configured symbols.

```bash
cd backend
python scheduler.py
```

To configure which symbols it watches, set `SYMBOLS` in `backend/config.py`.

---

### One-command startup (alternative)

Instead of Steps 5–6 separately, you can use the provided shell script from the project root:

```bash
./run.sh
```

This starts both the backend (port 5001) and the frontend (port 8080) in the background.

---

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Make sure you activated the venv (`source .venv/bin/activate`) and ran `pip install -r backend/requirements.txt` |
| `Address already in use` on port 5001 | Run `lsof -ti:5001 \| xargs kill -9` |
| `Address already in use` on port 8080 | Run `lsof -ti:8080 \| xargs kill -9` |
| Empty / null risk fields | Normal — means the trade failed the risk/reward filter (< 2:1 ratio or > 2% stop loss) |
| `decision: WAIT` always | Pattern confidence is below 0.6 threshold; the system is conservative by design |

---

## License

MIT