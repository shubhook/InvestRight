# InvestRight — Detailed Project Report

---

## 1. Project Idea

InvestRight is an AI-powered algorithmic trading assistant for Indian equity markets (NSE/BSE). The core idea is to replace human intuition with a data-driven, probabilistic decision engine that:

1. Continuously scans a watchlist of stocks.
2. Extracts technical signals and sentiment from news.
3. Applies a learned probabilistic model to decide whether to BUY, SELL, or WAIT.
4. Sizes positions using Kelly criterion and 2:1 risk/reward.
5. Places orders through a broker abstraction (paper simulation or live via Zerodha).
6. Monitors open positions and exits them automatically at stop loss or target.
7. Evaluates trade outcomes and uses gradient ascent to improve the decision model over time.
8. Provides LLM-generated explanations (via Groq) for every trade decision.

The system is designed for solo retail traders who want systematic, emotionless execution backed by a self-improving AI model.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                        Frontend                         │
│  frontend/app.js  (HTML/JS, port 8080)                  │
│  Dashboard · Watchlist · Portfolio · Backtest · Settings│
└────────────────────┬────────────────────────────────────┘
                     │ HTTP (JWT Bearer)
┌────────────────────▼────────────────────────────────────┐
│                    Flask API (port 5001)                 │
│  backend/main.py                                        │
│                                                         │
│  ┌──────────┐  ┌─────────────┐  ┌────────────────────┐ │
│  │  Auth    │  │  Pipeline   │  │  Other Endpoints   │ │
│  │  Layer   │  │  Endpoints  │  │  Watchlist/Orders/ │ │
│  │  JWT     │  │  /analyze   │  │  Portfolio/Backtest│ │
│  └──────────┘  └──────┬──────┘  └────────────────────┘ │
└─────────────────────  │  ──────────────────────────────┘
                        │
        ┌───────────────▼──────────────────────┐
        │           AI Pipeline                 │
        │                                       │
        │  Data Agent → Analysis Agent          │
        │       → Pattern Engine                │
        │       → Decision Agent                │
        │       → LLM Review/Explanation        │
        │       → Risk Engine                   │
        │       → Action Agent                  │
        └───────────────┬──────────────────────┘
                        │
          ┌─────────────▼──────────────┐
          │       Broker Layer          │
          │  PaperBroker │ KiteBroker  │
          │  (simulate)  │  (Zerodha)  │
          └─────────────┬──────────────┘
                        │
     ┌──────────────────▼────────────────────┐
     │              Data Layer               │
     │  PostgreSQL  │  Redis  │  memory.json │
     │  (orders,    │  (LTP   │  (trades,    │
     │   positions, │   cache)│   weights)   │
     │   watchlist, │         │              │
     │   backtests, │         │              │
     │   audit_log) │         │              │
     └───────────────────────────────────────┘
```

---

## 3. Key Concepts

### 3.1 Probabilistic Decision Model

The decision engine uses logistic regression. Every market signal is encoded as a numeric feature. A weighted sum passes through a sigmoid to produce P(up) — the probability the stock price will go up.

```
P(up) = sigmoid(w_bias + w_trend×trend + w_sentiment×sentiment
                + w_pattern×(direction × confidence)
                + w_volatility×vol_norm + w_sr_signal×sr_signal
                + w_volume×volume_signal)
```

Expected Value drives the final call:
```
EV = P(up) × ATR - P(down) × ATR
confidence = |EV| / current_price

if |confidence| > 0.005 → BUY or SELL
else                     → WAIT
```

### 3.2 Self-Learning via Gradient Ascent

After each trade resolves (target hit or stop hit), the outcome is labelled `correct` or `wrong`. The weights are updated using stochastic gradient ascent on binary cross-entropy:

```
error = y - sigmoid(z)
w[k] += learning_rate × error × feature[k]
```

Over time, the model learns which signals are actually predictive for each market regime.

### 3.3 Kelly Position Sizing

Position size is determined by the Kelly criterion — the theoretically optimal bet fraction given your edge and odds:

```
K = P(win) - P(loss) / RR
position_size = min(K, 0.5)   # capped at 50% of capital
```

This automatically sizes down when the model's confidence is low.

### 3.4 Risk-First Design

Every trade has three absolute rules:
- **Stop Loss** at the nearest S/R level (or 2×ATR fallback).
- **Target** at 2× the stop distance (2:1 reward-to-risk).
- **Max loss cap**: trades are rejected if max loss exceeds 10% of position.

The Exit Monitor runs every 15 minutes and closes positions automatically.

### 3.5 LLM Augmentation (Groq)

LLM calls are used in three places, all with rule-based fallbacks:
1. **Sentiment analysis** — news headlines → `positive / negative / neutral` (more nuanced than keyword matching).
2. **Decision review** — LLM checks if the decision is logically consistent with the signals.
3. **Trade explanation** — produces a human-readable thesis for why the trade was made.
4. **Portfolio narrative** — periodic summaries and daily briefs for the trader.

The Groq model `llama-3.1-8b-instant` is used for speed and low cost. All calls are capped at 30s and logged to the `llm_calls` DB table.

### 3.6 Broker Abstraction

The broker interface (`BaseBroker`) exposes four methods:
- `place_order(params)` → order result
- `get_order_status(broker_order_id)` → status
- `cancel_order(broker_order_id)` → bool
- `get_ltp(symbol)` → float | None

**PaperBroker**: orders fill instantly at limit price (entry from risk engine), recorded in PostgreSQL. Used for testing and simulation.

**KiteBroker**: wraps Zerodha KiteConnect. Uses OAuth for authentication. Supports both MARKET and LIMIT order types. Live P&L and holdings can be fetched directly.

The active broker is selected by `BROKER_MODE` env var and can be switched at runtime via `POST /broker/mode` without a server restart.

### 3.7 Observability and Tracing

Every pipeline run generates a UUID `trace_id`. All pipeline stages log structured events (component, event_type, message, duration_ms, severity) to the `audit_log` PostgreSQL table. The full trace is queryable via `GET /observability/trace/<trace_id>`, giving a per-run timeline with latencies.

### 3.8 Model Health Monitoring

`feedback/model_monitor.py` computes accuracy and Brier score over a rolling window of completed trades. The scheduler checks this every 15 minutes and auto-activates the kill switch if accuracy drops below threshold — preventing continued trading with a degraded model.

---

## 4. Technical Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.9, Flask, flask-cors |
| AI / ML | Custom logistic regression (NumPy), scikit-style gradient ascent |
| LLM | Groq API (llama-3.1-8b-instant) |
| Market Data | yfinance (primary), Alpha Vantage (fallback) |
| News | Google Finance RSS (primary), NewsAPI (fallback) |
| Live Broker | Zerodha KiteConnect (`kiteconnect` SDK) |
| Database | PostgreSQL (orders, positions, P&L, backtests, audit) |
| Cache | Redis (LTP cache, rate limiting) |
| Auth | JWT (PyJWT) |
| Scheduler | `schedule` library |
| Frontend | Vanilla HTML/JS, port 8080 |
| Package Mgmt | pip + requirements.txt, venv |

---

## 5. AI Pipeline — Stage by Stage

### Stage 1: Data Collection
- **File:** `backend/agents/data_agent.py`
- **Input:** Symbol string (e.g., `RELIANCE.NS`)
- **Output:** `{ symbol, ohlc: DataFrame, volume: Series, news: [str] }`
- Fetches 1-month of 1-hour OHLCV data. Validates ≥30 candles. Returns `None` on failure to halt the pipeline.

### Stage 2: Signal Extraction
- **File:** `backend/agents/analysis_agent.py`
- **Input:** Data package
- **Output:** `{ trend, support[], resistance[], volatility, sentiment, sentiment_source, volume_signal }`
- Trend: SMA-20 vs SMA-50. S/R: rolling window local extrema. ATR: 14-period. Sentiment: Groq LLM → keyword fallback.

### Stage 3: Pattern Detection
- **File:** `backend/utils/pattern_engine.py`
- **Input:** OHLCV DataFrame
- **Output:** `{ pattern, confidence, direction }`
- Geometric: Double Top, Ascending Triangle, Head & Shoulders. Momentum: RSI oversold/overbought, MACD crossover. Returns highest-confidence candidate ≥0.5.

### Stage 4: Decision
- **File:** `backend/agents/decision_agent.py`
- **Input:** Analysis + pattern + current price
- **Output:** `{ action, confidence, expected_value, probability_up, reason, features_vector }`
- Logistic regression over 7 features. Weights loaded from `weights.json` (updated by learning).

### Stage 4b: LLM Review & Explanation
- **Files:** `backend/llm/review_agent.py`, `backend/llm/explanation_agent.py`
- Groq LLM validates the decision and generates a human-readable thesis. Both are optional and degrade gracefully.

### Stage 5: Risk Management
- **File:** `backend/utils/risk_engine.py`
- **Input:** Decision + analysis + OHLCV
- **Output:** `{ action, entry, stop_loss, target, rr_ratio, max_loss_pct, position_size_fraction }`
- S/R-based stop loss, 2:1 target, Kelly sizing. Rejects if max loss >10% or Kelly ≤0.

### Stage 6: Execution
- **File:** `backend/agents/action_agent.py`
- **Input:** Risk-adjusted decision
- Stores trade in `memory.json`. Calls `get_broker().place_order(...)`. Records order in PostgreSQL `orders` table.

### Stage 7: Feedback
- **File:** `backend/agents/feedback_agent.py`
- Called by Exit Monitor (automated) or manual position close.
- Labels trade as `correct`, `wrong`, or `pending`. Updates `memory.json`.

### Stage 8: Weight Learning
- **File:** `backend/memory/weights_store.py`
- Called via `POST /update-weights` or post-evaluation.
- Gradient ascent on completed trades updates `weights.json`.

---

## 6. API Reference

### Public Endpoints (no auth)

| Endpoint | Method | Description |
|---|---|---|
| `GET /health` | GET | System status: DB, Redis, kill switch, model health, Kite token validity |
| `POST /token` | POST | Exchange `{ "api_key": "..." }` for JWT |
| `GET /kite/login` | GET | Get Zerodha OAuth login URL |
| `GET /kite/callback` | GET | Zerodha OAuth callback (exchanges request_token for access_token) |

### Pipeline Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /analyze?symbol=X` | GET | Full pipeline for one symbol → `{ decision, confidence, risk, analysis_summary, pattern_detected, execution, trace_id }` |
| `GET /sentiment` | GET | Read-only scan of all active watchlist symbols (no trade execution) |
| `POST /update-weights` | POST | Body: `{ "learning_rate": 0.01 }` — trigger gradient-ascent update |
| `POST /halt` | POST | Body: `{ "reason": "...", "activated_by": "..." }` — activate kill switch |
| `POST /resume` | POST | Deactivate kill switch |

### Order Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /orders` | GET | All orders, most recent first |
| `GET /orders/<order_id>` | GET | Single order detail |
| `POST /orders/<order_id>/cancel` | POST | Cancel an open order |

### Broker Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /broker/status` | GET | Broker mode, kill switch state, Kite connectivity |
| `POST /broker/mode` | POST | Body: `{ "mode": "paper" \| "live" }` — runtime broker switch |
| `POST /broker/kite/token` | POST | Body: `{ "access_token": "...", "request_token": "..." }` |

### Watchlist Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /watchlist` | GET | All watchlist entries |
| `POST /watchlist` | POST | Body: `{ "symbol": "RELIANCE.NS", "capital_pct": 10 }` |
| `DELETE /watchlist/<symbol>` | DELETE | Remove symbol |

### Portfolio Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /portfolio` | GET | Full summary: capital, P&L, positions, stats |
| `GET /portfolio/positions` | GET | Open positions with unrealised P&L |
| `GET /portfolio/positions/<id>` | GET | Single position detail |
| `POST /portfolio/positions/<id>/close` | POST | Manual close; body `{ "reason": "..." }` |
| `GET /portfolio/live` | GET | Live holdings from Zerodha (live mode only) |
| `GET /portfolio/pnl` | GET | Full P&L breakdown |
| `GET /portfolio/pnl/daily` | GET | Today's P&L |
| `GET /portfolio/summary?timeframe=7d` | GET | LLM-generated portfolio narrative |
| `GET /portfolio/daily-brief` | GET | LLM-generated daily brief |

### Observability Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /observability/trace/<trace_id>` | GET | Full event sequence + latency for one pipeline run |
| `GET /observability/metrics?minutes=60` | GET | Per-component latency and success rates |
| `GET /observability/audit` | GET | Query audit log by symbol, severity, event_type, since, limit |

### Backtest Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `POST /backtest/run` | POST | Launch async backtest; body: `{ symbol, start_date, end_date, interval?, initial_capital? }` |
| `GET /backtest/runs` | GET | List all runs (paginated: limit, offset) |
| `GET /backtest/runs/<run_id>` | GET | Full run summary (metrics, status) |
| `GET /backtest/runs/<run_id>/trades` | GET | Trade-by-trade breakdown |
| `GET /backtest/runs/<run_id>/equity-curve` | GET | Equity curve data points |
| `POST /backtest/compare` | POST | Body: `{ "run_ids": [...] }` — side-by-side comparison |
| `POST /backtest/walk-forward` | POST | Body: `{ symbol, start_date, end_date, n_splits?, interval?, initial_capital? }` |

---

## 7. Scheduler Jobs

`backend/scheduler.py` runs all background jobs. Symbols are read from the DB watchlist on every cycle.

| Job | Schedule | Purpose |
|---|---|---|
| Degradation check | Every 15 min | Compute model accuracy; auto-halt if degraded |
| Exit monitor | Every 15 min | Close positions at stop/target |
| Watchlist analysis | Every 15 min (market hours) | Run full pipeline per active symbol |
| Pending trade evaluation | Every 15 min (market hours) | Label pending trades correct/wrong |
| Daily P&L snapshot | 15:30 IST daily | Record end-of-day portfolio state |
| Log retention | 02:00 IST daily | Purge aged audit/LLM/metrics rows |
| DB cleanup | 03:00 IST daily | ANALYZE tables + reset stale backtest runs |

All jobs run once at startup, then on schedule.

---

## 8. Database Schema (Key Tables)

| Table | Purpose |
|---|---|
| `orders` | All broker orders (paper and live): status, fill price, quantity, broker_order_id |
| `positions` | Open and closed positions: entry, current price, P&L |
| `pnl_snapshots` | Daily end-of-day portfolio state snapshots |
| `watchlist` | Active symbols with capital allocation percentage |
| `capital_limits` | Per-symbol max capital percentage (synced from watchlist) |
| `backtest_runs` | Backtest metadata: symbol, date range, status, metrics |
| `backtest_trades` | Per-trade records within a backtest run |
| `audit_log` | Structured pipeline events with trace_id, severity, duration_ms |
| `llm_calls` | Per-call log: agent, model, latency, token counts, status |
| `pipeline_metrics` | Component-level latency and success aggregates |
| `kill_switch` | Kill switch state (activated, reason, activated_by, timestamp) |
| `idempotency_keys` | Duplicate order prevention |

---

## 9. Authentication Flow

```
Client                     Backend
  │                           │
  │  POST /token              │
  │  { api_key: "secret" }    │
  │ ─────────────────────────>│
  │                           │ Verify API_KEY env var
  │  { token: "eyJ...",       │
  │    expires_in_hours: 24 } │
  │ <─────────────────────────│
  │                           │
  │  GET /analyze?symbol=X    │
  │  Authorization: Bearer eyJ│
  │ ─────────────────────────>│
  │                           │ Verify JWT signature + expiry
  │  { decision: "BUY", ... } │
  │ <─────────────────────────│
```

Public endpoints (`/health`, `/token`, `/kite/login`, `/kite/callback`) require no auth.

---

## 10. Zerodha OAuth Flow

```
User                 Frontend             Backend              Zerodha
  │                     │                    │                    │
  │  Click "Connect"    │                    │                    │
  │ ─────────────────>  │                    │                    │
  │                     │  GET /kite/login   │                    │
  │                     │ ─────────────────> │                    │
  │                     │  { login_url }     │                    │
  │                     │ <───────────────── │                    │
  │                     │  redirect user     │                    │
  │ <───────────────────│                    │                    │
  │  Zerodha login page │                    │                    │
  │ ─────────────────────────────────────────────────────────── > │
  │  User logs in       │                    │                    │
  │ < ─────────────────────────────────────────────────────────── │
  │  Redirect to /kite/callback?request_token=XXX                 │
  │                     │                    │ generate_session() │
  │                     │                    │ ──────────────────>│
  │                     │                    │ { access_token }   │
  │                     │                    │ <──────────────────│
  │                     │                    │ store_token()      │
  │  Redirect to /?kite=connected            │                    │
  │ <─────────────────────────────────────── │                    │
```

---

## 11. Decision Pipeline — Data Flow Diagram

```
Symbol ("RELIANCE.NS")
        │
        ▼
[Data Agent]
  yfinance → OHLCV DataFrame (1h, 1 month)
  News RSS → ["headline 1", ...]
        │
        ▼
[Analysis Agent]
  trend        = SMA20 vs SMA50     → "uptrend" | "downtrend"
  support[]    = rolling minima (w=10)
  resistance[] = rolling maxima (w=10)
  volatility   = ATR(14)
  sentiment    = Groq LLM → keyword fallback
  volume_sig   = (vol - avg20) / avg20  ∈ [-2, 2]
        │
        ▼
[Pattern Engine]
  Geometric: Double Top | Ascending Triangle | Head & Shoulders
  Momentum:  RSI<30 | RSI>70 | MACD cross
  → { pattern, confidence, direction }
        │
        ▼
[Decision Agent]
  z = Σ w[k] × feature[k]   (weights from weights.json)
  P(up) = sigmoid(z)
  EV    = P(up)×ATR - P(down)×ATR
  conf  = |EV| / price
  → BUY | SELL | WAIT
        │
        ▼
[LLM Review + Explanation]  (optional, Groq)
        │
        ▼
[Risk Engine]
  stop_loss  = nearest S/R (or ±2×ATR)
  target     = entry ± 2×(entry - stop)
  kelly      = P(win) - P(loss)/RR
  size       = min(kelly, 0.5)
  → Reject if max_loss > 10% or kelly ≤ 0
        │
        ▼
[Action Agent]
  WAIT → log, return executed=False
  BUY/SELL → store in memory.json → place_order(broker)
        │
        ▼
[PaperBroker / KiteBroker]
  Fill at limit price → write to orders table
        │
        ▼
[Position Manager]
  Record open position → monitor every 15 min
        │
        ▼
[Exit Monitor (scheduler)]
  current_price ≥ target  → close + label "correct"
  current_price ≤ stop    → close + label "wrong"
        │
        ▼
[Weights Store]
  gradient ascent on labelled trades → update weights.json
```

---

## 12. Frontend Overview

`frontend/app.js` is a single-page application with five tabs:

| Tab | Purpose |
|---|---|
| Dashboard | Analyze a symbol on demand; shows decision, risk params, analysis summary, pattern |
| Trade Setup | Detailed trade setup with LLM explanation |
| Watchlist | Add/remove symbols; view active watchlist with capital allocation |
| Portfolio | Open positions, P&L, daily brief, portfolio summary |
| Settings | Broker mode toggle, Zerodha connection, kill switch control |

The frontend authenticates with `POST /token` on load (API key from local config), then attaches the JWT as a Bearer token on all subsequent calls.

---

## 13. Configuration & Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `API_KEY` | Required — master key to obtain JWT | (none) |
| `JWT_EXPIRY_HOURS` | JWT lifetime | 24 |
| `CORS_ORIGINS` | Allowed CORS origins | `http://localhost:3000,http://localhost:8080` |
| `FLASK_DEBUG` | Enable Flask debug mode | `false` |
| `BROKER_MODE` | `paper` or `live` | `paper` |
| `TOTAL_CAPITAL` | Total trading capital in INR | 0 |
| `KITE_API_KEY` | Zerodha API key | (none) |
| `KITE_API_SECRET` | Zerodha API secret | (none) |
| `FRONTEND_URL` | Frontend base URL (for OAuth redirect) | `http://localhost:8080` |
| `GROQ_API_KEY` | Groq API key for LLM features | (none — LLM disabled if absent) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `DATABASE_URL` | PostgreSQL connection string | (none) |
| `BACKTEST_DEFAULT_CAPITAL` | Default initial capital for backtests | 100000 |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage fallback key | (none) |
| `NEWS_API_KEY` | NewsAPI fallback key | (none) |

---

## 14. File Structure

```
project/
├── backend/
│   ├── main.py                    # Flask app + all API routes
│   ├── config.py                  # Config class + env validation
│   ├── scheduler.py               # Background job scheduler
│   ├── requirements.txt
│   ├── agents/
│   │   ├── data_agent.py          # Stage 1 — data collection
│   │   ├── analysis_agent.py      # Stage 2 — signal extraction
│   │   ├── decision_agent.py      # Stage 4 — probabilistic decision
│   │   ├── action_agent.py        # Stage 6 — execution + storage
│   │   └── feedback_agent.py      # Stage 7 — outcome labelling
│   ├── utils/
│   │   ├── pattern_engine.py      # Stage 3 — pattern detection
│   │   ├── risk_engine.py         # Stage 5 — risk management
│   │   ├── market_hours.py        # NSE market hours check
│   │   ├── rate_limiter.py        # Per-IP rate limiting (Redis)
│   │   └── logger.py              # Structured logging
│   ├── llm/
│   │   ├── llm_client.py          # Central Groq API wrapper
│   │   ├── sentiment_agent.py     # LLM news sentiment
│   │   ├── review_agent.py        # LLM decision review
│   │   ├── explanation_agent.py   # LLM trade explanation
│   │   └── summary_agent.py       # LLM portfolio narrative
│   ├── broker/
│   │   ├── base.py                # BaseBroker interface
│   │   ├── broker_factory.py      # Broker selection by BROKER_MODE
│   │   ├── paper_broker.py        # Simulated paper trading
│   │   ├── kite_broker.py         # Zerodha KiteConnect live trading
│   │   └── order_manager.py       # Order lifecycle helpers
│   ├── auth/
│   │   ├── jwt_handler.py         # JWT generation + verification
│   │   ├── middleware.py          # @require_auth decorator
│   │   └── kite_token_refresh.py  # Kite access token storage
│   ├── portfolio/
│   │   ├── position_manager.py    # Open/close positions
│   │   ├── pnl_calculator.py      # Realised/unrealised P&L
│   │   ├── exit_monitor.py        # Automated stop/target exits
│   │   └── capital_account.py     # Capital allocation
│   ├── safety/
│   │   ├── kill_switch.py         # Trading halt/resume
│   │   ├── capital_limits.py      # Per-symbol caps
│   │   └── idempotency.py         # Duplicate prevention
│   ├── observability/
│   │   ├── trace.py               # TraceContext + trace_id
│   │   ├── audit_log.py           # Structured event logging
│   │   └── metrics.py             # Component latency aggregates
│   ├── feedback/
│   │   └── model_monitor.py       # Accuracy + Brier score
│   ├── backtest/
│   │   ├── backtest_engine.py     # Historical pipeline replay
│   │   ├── data_loader.py         # Historical OHLCV loader
│   │   ├── walk_forward.py        # Walk-forward validation
│   │   ├── performance.py         # Sharpe, drawdown, win rate
│   │   └── report_generator.py    # Summaries, curves, comparison
│   ├── memory/
│   │   ├── memory_store.py        # Trade ledger (memory.json)
│   │   ├── memory_reader.py       # Read-only trade queries
│   │   └── weights_store.py       # Stage 8 — weight learning
│   ├── db/
│   │   ├── connection.py          # PostgreSQL db_cursor()
│   │   └── init_db.py             # Schema init
│   ├── cache/
│   │   └── redis_client.py        # Redis LTP cache + rate limit
│   ├── services/
│   │   ├── stock_service.py       # yfinance / Alpha Vantage
│   │   └── news_service.py        # Google RSS / NewsAPI
│   ├── maintenance/
│   │   ├── log_retention.py       # Purge aged log rows
│   │   └── db_cleanup.py          # ANALYZE + stale cleanup
│   └── docs/
│       ├── agents.md
│       ├── system_design.md
│       └── bugfixes.md
├── frontend/
│   └── app.js                     # Single-page web UI
├── database/
│   ├── schema.sql                 # Full PostgreSQL schema
│   └── migrations/
│       └── 001_watchlist.sql
├── Docs/
│   ├── Pipeline.md                # Pipeline deep-dive
│   └── ProjectReport.md           # This document
├── run.sh                         # One-command startup
└── README.md
```

---

## 15. Running the System

### Prerequisites
- Python 3.9+
- PostgreSQL running locally (or `DATABASE_URL` set)
- Redis running locally (or `REDIS_URL` set)
- `.env` file with required variables (at minimum: `API_KEY`, `DATABASE_URL`)

### Start
```bash
./run.sh
# OR
cd backend && python main.py        # API on :5001
# In another terminal:
cd frontend && python -m http.server 8080
```

### First Use
1. Get a JWT: `POST /token` with `{ "api_key": "<your API_KEY>" }`
2. Add symbols to the watchlist: `POST /watchlist` with `{ "symbol": "RELIANCE.NS", "capital_pct": 20 }`
3. Run analysis: `GET /analyze?symbol=RELIANCE.NS`
4. Open the frontend at `http://localhost:8080`

### Optional: Enable Live Trading
1. Set `KITE_API_KEY` and `KITE_API_SECRET` in `.env`
2. Connect Zerodha account via Settings tab (OAuth flow)
3. Switch broker mode: `POST /broker/mode` with `{ "mode": "live" }`
