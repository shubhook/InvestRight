# InvestRight — Pipeline Overview

This document describes how data flows through the InvestRight AI trading system from a raw stock symbol to a trade decision and eventual learning feedback.

---

## High-Level Flow

```
Stock Symbol
    │
    ▼
[Auth]  JWT middleware — blocks unauthenticated requests
    │
    ▼
[Safety] Kill switch check — halts pipeline if trading is paused
    │
    ▼
[1] Data Agent          → Fetch OHLCV + news
    │
    ▼
[2] Analysis Agent      → Extract signals (trend, S/R, ATR, LLM sentiment, volume)
    │
    ▼
[3] Pattern Engine      → Detect chart/momentum patterns
    │
    ▼
[4] Decision Agent      → Compute P(up), EV → BUY / SELL / WAIT
    │
    ▼
[LLM Review]            → Optional: Groq LLM review & explanation of decision
    │
    ▼
[5] Risk Engine         → Set entry, stop loss, target, Kelly position size
    │
    ▼
[6] Action Agent        → Store trade; call broker to place order
    │
    ▼
[Broker]                → PaperBroker (simulated) or KiteBroker (Zerodha live)
    │
    ▼
[7] Feedback Agent      → Evaluate outcome → correct / wrong / pending
    │
    ▼
[8] Weights Store       → Gradient-ascent learning → update weights.json
```

---

## Stage-by-Stage Breakdown

### Auth — `backend/auth/`

All protected endpoints require a Bearer JWT obtained from `POST /token`.

- `POST /token` exchanges the `API_KEY` env var for a JWT (expiry set by `JWT_EXPIRY_HOURS`, default 24h).
- `backend/auth/jwt_handler.py` generates and verifies tokens.
- `backend/auth/middleware.py` provides the `@require_auth` decorator used on every protected route.
- Public endpoints (no auth): `/health`, `/token`, `/kite/login`, `/kite/callback`.

---

### Safety — `backend/safety/`

**Kill Switch** (`backend/safety/kill_switch.py`)
- `POST /halt` immediately blocks all `/analyze` pipeline calls.
- `POST /resume` restores normal operation.
- Scheduler auto-activates the kill switch if model accuracy degrades below threshold (via `check_and_halt_if_degraded`).
- Exit monitor is NOT blocked by the kill switch — positions can always be closed.

**Capital Limits** (`backend/safety/capital_limits.py`)
- Per-symbol capital allocation caps, synced from the watchlist table.

**Idempotency** (`backend/safety/idempotency.py`)
- Guards against duplicate order placement.

---

### 1. Data Agent — `backend/agents/data_agent.py`

**Input:** Stock symbol string (e.g., `RELIANCE.NS`)

**What it does:**
- Fetches 1-hour OHLCV data for the last 1 month via `yfinance` (primary) or Alpha Vantage (fallback).
- Fetches latest news headlines from Google Finance RSS (primary) or NewsAPI (fallback).
- Validates the data: requires at least 30 candles and all OHLC columns present.

**Output:**
```python
{
  "symbol": "RELIANCE.NS",
  "ohlc": DataFrame,   # columns: open, high, low, close
  "volume": Series,
  "news": ["Headline 1", "Headline 2", ...]
}
```

Returns `None` on failure, which halts the pipeline for that symbol.

---

### 2. Analysis Agent — `backend/agents/analysis_agent.py`

**Input:** Data package from Stage 1

**What it does:**
- **Trend:** SMA-20 vs SMA-50 crossover → `"uptrend"` or `"downtrend"`
- **Support / Resistance:** Rolling window (size=10) local minima/maxima → lists of price levels
- **Volatility:** 14-period ATR in price units
- **Sentiment:** First attempts LLM-based sentiment via Groq (`llm/sentiment_agent.py`); falls back to keyword scoring if GROQ_API_KEY is absent or call fails. `sentiment_source` field indicates `"llm"` or `"keyword"`.
- **Volume Signal:** `(current_vol - avg_vol_20) / avg_vol_20`, clipped to `±2.0`

**Output:**
```python
{
  "trend": "uptrend" | "downtrend",
  "support": [95.50, 96.20, ...],
  "resistance": [105.30, 106.10, ...],
  "volatility": 1.25,          # ATR
  "sentiment": "positive" | "negative" | "neutral",
  "sentiment_source": "llm" | "keyword",
  "volume_signal": 0.5
}
```

---

### 3. Pattern Engine — `backend/utils/pattern_engine.py`

**Input:** OHLCV data

**What it does:**

Detects two categories of patterns:

**Geometric Patterns:**
| Pattern | Direction | Detection Criteria |
|---|---|---|
| Double Top | Bearish | Two peaks within ±2%, valley ≥3% below, lower volume on 2nd peak |
| Ascending Triangle | Bullish | Flat resistance (slope ≤0.1%), rising support (slope > 0), multiple touches |
| Head & Shoulders | Bearish | Head > both shoulders, shoulders within 5% symmetry |

**Momentum Signals:**
| Signal | Direction | Trigger |
|---|---|---|
| RSI Oversold | Bullish | RSI < 30 |
| RSI Overbought | Bearish | RSI > 70 |
| MACD Bullish Crossover | Bullish | MACD crosses above signal line |
| MACD Bearish Crossover | Bearish | MACD crosses below signal line |

All candidates with confidence ≥ 0.5 are collected; the highest-confidence candidate is returned.

**Output:**
```python
{
  "pattern": "ascending_triangle" | "double_top" | "head_and_shoulders" |
             "rsi_oversold" | "rsi_overbought" |
             "macd_bullish_crossover" | "macd_bearish_crossover" | "none",
  "confidence": 0.81,    # 0.0–1.0
  "direction": "bullish" | "bearish" | "neutral"
}
```

---

### 4. Decision Agent — `backend/agents/decision_agent.py`

**Input:** Analysis signals + pattern output + current price

**What it does:**

Uses a logistic regression model to compute the probability that price goes up:

```
z = w_bias + w_trend×trend + w_sentiment×sentiment
    + w_pattern×(direction × confidence)
    + w_volatility×vol_norm + w_sr_signal×sr_signal
    + w_volume×volume_signal

P(up) = sigmoid(z)
```

Default weights:
| Weight | Value |
|---|---|
| w_bias | 0.1 |
| w_trend | 1.2 |
| w_sentiment | 0.8 |
| w_pattern | 1.5 |
| w_volatility | -0.5 |
| w_sr_signal | 1.0 |
| w_volume | 0.3 |

Expected Value and decision rule:
```
EV = P(up) × ATR - P(down) × ATR
confidence = |EV| / current_price    # dimensionless, cross-symbol comparable

if |confidence| > 0.005:
    action = "BUY" if EV > 0 else "SELL"
else:
    action = "WAIT"
```

**Output:**
```python
{
  "action": "BUY" | "SELL" | "WAIT",
  "confidence": 0.05,
  "expected_value": 2.45,
  "probability_up": 0.65,
  "reason": "...",
  "features_vector": {...}    # saved for later weight learning
}
```

---

### LLM Layer — `backend/llm/`

Optional enrichment using the Groq API (`llama-3.1-8b-instant` by default, 30s timeout, no retries). All agents degrade gracefully — if `GROQ_API_KEY` is not set or the call fails, a rule-based fallback is used. Every call is logged to the `llm_calls` DB table.

| Agent | File | When called | Purpose |
|---|---|---|---|
| Sentiment Agent | `llm/sentiment_agent.py` | Stage 2 (Analysis) | LLM-based news sentiment (replaces keyword fallback) |
| Review Agent | `llm/review_agent.py` | After Stage 4 (Decision) | Validates decision logic |
| Explanation Agent | `llm/explanation_agent.py` | After Stage 4 | Human-readable explanation of the trade thesis |
| Summary Agent | `llm/summary_agent.py` | `/portfolio/summary`, `/portfolio/daily-brief` | LLM narrative of portfolio state |

Central wrapper: `backend/llm/llm_client.py` — all agents call this, never the Groq API directly.

---

### 5. Risk Engine — `backend/utils/risk_engine.py`

**Input:** Decision output + analysis signals + OHLCV data

**What it does:**

- **Stop Loss:**
  - BUY: nearest support level below entry price
  - SELL: nearest resistance level above entry price
  - Fallback: entry ± 2×ATR if no S/R levels available

- **Target:** 2:1 reward-to-risk ratio
  - BUY: `entry + 2 × (entry - stop_loss)`
  - SELL: `entry - 2 × (entry - stop_loss)`

- **Validation:**
  - Rejects trade if max loss > 10% (hard cap)
  - Computes Kelly fraction: `K = P(win) - P(loss) / RR`
  - Rejects if Kelly ≤ 0 (negative expected value)
  - Position size = `min(Kelly, 0.5)`

**Output:**
```python
{
  "action": "BUY" | "SELL" | "WAIT",
  "entry": 2450.0,
  "stop_loss": 2390.0,
  "target": 2570.0,
  "rr_ratio": 2.0,
  "max_loss_pct": 2.45,
  "position_size_fraction": 0.25,
  "rejection_reason": None
}
```

---

### 6. Action Agent — `backend/agents/action_agent.py`

**Input:** Risk-adjusted decision

**What it does:**
- If action is `WAIT`: logs reason, returns `executed=False`.
- If action is `BUY` or `SELL`:
  - Generates a UUID `trade_id`
  - Creates a trade record (all risk params + `features_vector`)
  - Stores the record in `memory/memory.json` via `memory_store.py`
  - Calls the active broker (`get_broker()`) to place a limit order
  - Returns `executed=True` with `trade_id` and broker `order_id`

**Stored trade structure:**
```python
{
  "trade_id": "uuid",
  "timestamp": "ISO datetime",
  "symbol": "RELIANCE.NS",
  "action": "BUY",
  "entry": 2450.0,
  "stop_loss": 2390.0,
  "target": 2570.0,
  "rr_ratio": 2.0,
  "position_size_fraction": 0.25,
  "features_vector": {...},
  "result": None              # filled in by Feedback Agent later
}
```

---

### Broker Layer — `backend/broker/`

Abstracted behind `broker/broker_factory.py` — the active broker is selected by the `BROKER_MODE` env var (`"paper"` or `"live"`). Mode can be toggled at runtime via `POST /broker/mode` without restarting.

**PaperBroker** (`broker/paper_broker.py`)
- Simulated broker — no real money, no external API calls.
- Orders fill immediately at the limit/entry price; falls back to LTP from Redis cache or yfinance.
- Order records are written to the `orders` DB table with `broker_mode = "paper"`.

**KiteBroker** (`broker/kite_broker.py`)
- Live broker using Zerodha KiteConnect API.
- Requires `KITE_API_KEY`, `KITE_API_SECRET`, and a valid access token (stored/refreshed via `auth/kite_token_refresh.py`).
- Supports `place_order`, `get_order_status`, `cancel_order`, `get_ltp`, `get_portfolio`.
- Switching to live mode is blocked if no valid Kite token exists.

**Zerodha OAuth Flow:**
1. Frontend calls `GET /kite/login` → gets Zerodha login URL.
2. User logs in on Zerodha; Zerodha redirects to `GET /kite/callback?request_token=XXX`.
3. Backend exchanges `request_token` for `access_token` (KiteConnect `generate_session`).
4. Token is persisted via `auth/kite_token_refresh.py`.

**Order Management endpoints:**
- `GET /orders` — list all orders
- `GET /orders/<order_id>` — single order detail
- `POST /orders/<order_id>/cancel` — cancel an open order
- `GET /broker/status` — current broker mode + kill switch state
- `POST /broker/mode` — switch between `"paper"` and `"live"`
- `POST /broker/kite/token` — manually store a Kite access token

---

### 7. Feedback Agent — `backend/agents/feedback_agent.py`

**Input:** Current price for a previously executed trade

**What it does:**
- For BUY trades:
  - `current_price >= target` → `result = "correct"`
  - `current_price <= stop_loss` → `result = "wrong"`
  - Otherwise → `result = "pending"`
- For SELL trades: logic is reversed.
- Updates the trade record in `memory.json` with the result.
- Also invoked by the Exit Monitor and manual position close flow.

---

### 8. Weights Store — `backend/memory/weights_store.py`

**Input:** All trades with `result = "correct"` or `"wrong"`

**What it does:**

Runs stochastic gradient ascent on binary cross-entropy to improve the decision model:

```
y = 1  if (BUY & correct) or (SELL & wrong)
y = 0  otherwise

z = sum(w[k] × feature[k])
p = sigmoid(z)
error = y - p

for each weight k:
    w[k] += learning_rate × error × feature[k]
```

Updated weights are persisted to `weights.json` and loaded at the start of each subsequent analysis.

**Triggered via:** `POST /update-weights` with optional `{ "learning_rate": 0.01 }`

---

## Portfolio Management — `backend/portfolio/`

| Module | Purpose |
|---|---|
| `portfolio/position_manager.py` | Open/close positions; track entry price, quantity, status |
| `portfolio/pnl_calculator.py` | Realised and unrealised P&L; daily snapshots; portfolio summary |
| `portfolio/exit_monitor.py` | Checks open positions against stop loss / target every 15 min; triggers closes |
| `portfolio/capital_account.py` | Capital allocation tracking |

**Endpoints:**
- `GET /portfolio` — full portfolio summary (capital, P&L, positions, trade stats)
- `GET /portfolio/positions` — open positions with unrealised P&L
- `GET /portfolio/positions/<id>` — single position detail
- `POST /portfolio/positions/<id>/close` — manual close (not blocked by kill switch)
- `GET /portfolio/live` — live holdings/positions from Zerodha (live mode only)
- `GET /portfolio/pnl` — full P&L breakdown
- `GET /portfolio/pnl/daily` — today's P&L
- `GET /portfolio/summary` — LLM-generated portfolio narrative (`?timeframe=7d`)
- `GET /portfolio/daily-brief` — LLM-generated daily trading brief

---

## Observability — `backend/observability/`

Every pipeline run generates a `trace_id` (UUID). All stages log events to the `audit_log` DB table via `observability/audit_log.py`.

| Module | Purpose |
|---|---|
| `observability/trace.py` | `TraceContext` — carries trace_id + elapsed time through pipeline stages |
| `observability/audit_log.py` | Structured event logging to `audit_log` DB table |
| `observability/metrics.py` | Per-component latency and success rate aggregates |

**Endpoints:**
- `GET /observability/trace/<trace_id>` — full event sequence for one pipeline run
- `GET /observability/metrics?minutes=60` — per-component latency and success rates
- `GET /observability/audit` — filtered audit log query (symbol, severity, event_type, since, limit)

---

## Backtesting — `backend/backtest/`

Async backtest engine — runs in a background thread, results stored in the DB.

| Module | Purpose |
|---|---|
| `backtest/data_loader.py` | Load historical OHLCV data for a date range |
| `backtest/backtest_engine.py` | Replay the full pipeline (analysis → decision → risk) over historical data |
| `backtest/walk_forward.py` | Walk-forward validation (time-series cross-validation, configurable splits) |
| `backtest/performance.py` | Sharpe ratio, max drawdown, win rate, expectancy |
| `backtest/report_generator.py` | Run summaries, trade breakdowns, equity curves, multi-run comparison |

**Endpoints:**
- `POST /backtest/run` — launch async backtest (returns `run_id` immediately)
- `GET /backtest/runs` — list all runs (paginated)
- `GET /backtest/runs/<run_id>` — full summary for one run
- `GET /backtest/runs/<run_id>/trades` — trade-by-trade breakdown
- `GET /backtest/runs/<run_id>/equity-curve` — equity curve data points
- `POST /backtest/compare` — side-by-side comparison of multiple runs
- `POST /backtest/walk-forward` — launch async walk-forward validation

---

## Model Health — `backend/feedback/model_monitor.py`

- `compute_accuracy_window(n_days)` — computes accuracy and Brier score over the last N days of completed trades.
- Accuracy and Brier score are included in the `GET /health` response under `model_health`.
- Scheduler calls `check_and_halt_if_degraded()` every 15 min; auto-activates the kill switch if the model is unhealthy.

---

## Infrastructure

### Database (PostgreSQL)
- Persistent storage for orders, positions, P&L snapshots, backtest runs, audit log, LLM call log, watchlist, capital limits.
- `backend/db/connection.py` — `db_cursor()` context manager.
- `backend/db/init_db.py` — schema initialisation.
- `database/schema.sql` — full schema.

### Cache (Redis)
- LTP (Last Traded Price) cache — 60s TTL, shared between PaperBroker and KiteBroker.
- Rate limiting — sliding window per IP per endpoint.
- `backend/cache/redis_client.py`.

### Rate Limiting — `backend/utils/rate_limiter.py`
- Applied on `/analyze`, `/backtest/run`, `/backtest/walk-forward`.
- Per-IP sliding window; returns `Retry-After` headers on 429.

### Maintenance — `backend/maintenance/`
- `log_retention.py` — deletes aged rows from `audit_log`, `pipeline_metrics`, `llm_calls`, `rate_limit` tables. Runs daily at 02:00 IST.
- `db_cleanup.py` — `ANALYZE` tables and reset stale backtest runs. Runs daily at 03:00 IST.

---

## Execution Modes

### On-Demand (API)
```
GET http://localhost:5001/analyze?symbol=RELIANCE.NS
Authorization: Bearer <jwt>
```
Runs the full pipeline (Stages 1–6 + Broker) for one symbol and returns a JSON response with `trace_id`.

### Sentiment Scan (Read-Only)
```
GET http://localhost:5001/sentiment
```
Scans all active watchlist symbols concurrently (Stages 1–4 only). Does NOT execute trades or check the kill switch. Returns bullish/bearish signals for each symbol.

### Automated (Scheduler)
`backend/scheduler.py` runs the following jobs:

| Job | Frequency | Notes |
|---|---|---|
| Degradation check | Every 15 min | Auto-halts if model accuracy degrades |
| Exit monitor | Every 15 min | Checks open positions against stop/target |
| Watchlist analysis | Every 15 min | Reads active symbols from DB; market hours only |
| Pending trade evaluation | Every 15 min | Updates result for pending trades; market hours only |
| Daily P&L snapshot | Daily 15:30 IST | Records end-of-day portfolio state |
| Log retention | Daily 02:00 IST | Purges aged audit/LLM/metrics rows |
| DB cleanup | Daily 03:00 IST | ANALYZE + reset stale backtest runs |

Symbols are read from the DB watchlist each cycle, so adding/removing symbols via the UI takes effect on the next run without a restart. Falls back to `Config.SYMBOLS` if the watchlist is empty.

### One-Command Startup
```bash
./run.sh
```
Starts the Flask backend on port 5001 and the frontend on port 8080.

---

## API Endpoint Summary

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | No | System status (DB, Redis, kill switch, model health, Kite token) |
| `/token` | POST | No | Exchange API key for JWT |
| `/analyze` | GET | Yes | Full pipeline for one symbol |
| `/sentiment` | GET | Yes | Read-only scan of all watchlist symbols |
| `/update-weights` | POST | Yes | Trigger gradient-ascent weight update |
| `/halt` | POST | Yes | Activate kill switch |
| `/resume` | POST | Yes | Deactivate kill switch |
| `/orders` | GET | Yes | List all orders |
| `/orders/<id>` | GET | Yes | Single order detail |
| `/orders/<id>/cancel` | POST | Yes | Cancel an open order |
| `/broker/status` | GET | Yes | Broker mode + system state |
| `/broker/mode` | POST | Yes | Switch paper ↔ live |
| `/broker/kite/token` | POST | Yes | Store Kite access token |
| `/kite/login` | GET | No | Get Zerodha OAuth login URL |
| `/kite/callback` | GET | No | Zerodha OAuth callback |
| `/watchlist` | GET | Yes | List watchlist |
| `/watchlist` | POST | Yes | Add/update symbol in watchlist |
| `/watchlist/<symbol>` | DELETE | Yes | Remove symbol from watchlist |
| `/portfolio` | GET | Yes | Full portfolio summary |
| `/portfolio/positions` | GET | Yes | Open positions + unrealised P&L |
| `/portfolio/positions/<id>` | GET | Yes | Single position detail |
| `/portfolio/positions/<id>/close` | POST | Yes | Manual position close |
| `/portfolio/live` | GET | Yes | Live holdings from Zerodha |
| `/portfolio/pnl` | GET | Yes | Full P&L breakdown |
| `/portfolio/pnl/daily` | GET | Yes | Today's P&L |
| `/portfolio/summary` | GET | Yes | LLM portfolio narrative |
| `/portfolio/daily-brief` | GET | Yes | LLM daily brief |
| `/observability/trace/<id>` | GET | Yes | Full trace event sequence |
| `/observability/metrics` | GET | Yes | Per-component latency stats |
| `/observability/audit` | GET | Yes | Filtered audit log query |
| `/backtest/run` | POST | Yes | Launch async backtest |
| `/backtest/runs` | GET | Yes | List backtest runs |
| `/backtest/runs/<id>` | GET | Yes | Backtest run summary |
| `/backtest/runs/<id>/trades` | GET | Yes | Trade breakdown |
| `/backtest/runs/<id>/equity-curve` | GET | Yes | Equity curve |
| `/backtest/compare` | POST | Yes | Compare multiple runs |
| `/backtest/walk-forward` | POST | Yes | Launch walk-forward validation |

---

## Component Map

| File | Role |
|---|---|
| `backend/main.py` | Flask API + pipeline orchestrator |
| `backend/agents/data_agent.py` | Stage 1 — data collection |
| `backend/agents/analysis_agent.py` | Stage 2 — signal extraction |
| `backend/utils/pattern_engine.py` | Stage 3 — pattern detection |
| `backend/agents/decision_agent.py` | Stage 4 — probabilistic decision |
| `backend/utils/risk_engine.py` | Stage 5 — risk management |
| `backend/agents/action_agent.py` | Stage 6 — trade execution & storage |
| `backend/agents/feedback_agent.py` | Stage 7 — outcome evaluation |
| `backend/memory/weights_store.py` | Stage 8 — weight learning |
| `backend/memory/memory_store.py` | Trade ledger (memory.json) |
| `backend/memory/memory_reader.py` | Read-only trade queries |
| `backend/llm/llm_client.py` | Central Groq API wrapper |
| `backend/llm/sentiment_agent.py` | LLM news sentiment |
| `backend/llm/review_agent.py` | LLM decision review |
| `backend/llm/explanation_agent.py` | LLM trade explanation |
| `backend/llm/summary_agent.py` | LLM portfolio narrative |
| `backend/broker/base.py` | BaseBroker interface |
| `backend/broker/broker_factory.py` | Selects active broker from BROKER_MODE |
| `backend/broker/paper_broker.py` | Simulated broker (no real orders) |
| `backend/broker/kite_broker.py` | Zerodha KiteConnect live broker |
| `backend/broker/order_manager.py` | Order lifecycle helpers |
| `backend/auth/jwt_handler.py` | JWT generation and verification |
| `backend/auth/middleware.py` | `@require_auth` decorator |
| `backend/auth/kite_token_refresh.py` | Kite access token storage/validation |
| `backend/portfolio/position_manager.py` | Open/close position tracking |
| `backend/portfolio/pnl_calculator.py` | Realised/unrealised P&L + snapshots |
| `backend/portfolio/exit_monitor.py` | Automated stop/target exit checks |
| `backend/portfolio/capital_account.py` | Capital allocation tracking |
| `backend/safety/kill_switch.py` | Trading halt/resume |
| `backend/safety/capital_limits.py` | Per-symbol capital caps |
| `backend/safety/idempotency.py` | Duplicate order prevention |
| `backend/observability/trace.py` | TraceContext — trace_id propagation |
| `backend/observability/audit_log.py` | Structured event logging |
| `backend/observability/metrics.py` | Component latency/success aggregates |
| `backend/feedback/model_monitor.py` | Accuracy + Brier score monitoring |
| `backend/backtest/backtest_engine.py` | Historical pipeline replay |
| `backend/backtest/data_loader.py` | Historical OHLCV loader |
| `backend/backtest/walk_forward.py` | Walk-forward validation |
| `backend/backtest/performance.py` | Sharpe, drawdown, win rate |
| `backend/backtest/report_generator.py` | Run summaries, equity curves, comparison |
| `backend/db/connection.py` | PostgreSQL connection (db_cursor) |
| `backend/db/init_db.py` | Schema initialisation |
| `backend/cache/redis_client.py` | Redis LTP cache |
| `backend/utils/rate_limiter.py` | Per-IP rate limiting (Redis) |
| `backend/utils/market_hours.py` | NSE market hours check |
| `backend/utils/logger.py` | Structured logging setup |
| `backend/maintenance/log_retention.py` | Purge aged log rows |
| `backend/maintenance/db_cleanup.py` | ANALYZE + stale run cleanup |
| `backend/services/stock_service.py` | yfinance / Alpha Vantage fetcher |
| `backend/services/news_service.py` | Google Finance RSS / NewsAPI fetcher |
| `backend/scheduler.py` | Multi-job automated scheduler |
| `backend/config.py` | Config + env var validation |
| `frontend/app.js` | Web UI — dashboard, watchlist, portfolio, settings |
