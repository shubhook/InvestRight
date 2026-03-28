# InvestRight — Pipeline Overview

This document describes how data flows through the InvestRight AI trading system from a raw stock symbol to a trade decision and eventual learning feedback.

---

## High-Level Flow

```
Stock Symbol
    │
    ▼
[1] Data Agent          → Fetch OHLCV + news
    │
    ▼
[2] Analysis Agent      → Extract signals (trend, S/R, ATR, sentiment, volume)
    │
    ▼
[3] Pattern Engine      → Detect chart/momentum patterns
    │
    ▼
[4] Decision Agent      → Compute P(up), EV → BUY / SELL / WAIT
    │
    ▼
[5] Risk Engine         → Set entry, stop loss, target, Kelly position size
    │
    ▼
[6] Action Agent        → Store trade in memory.json
    │
    ▼
[7] Feedback Agent      → Evaluate outcome → correct / wrong / pending
    │
    ▼
[8] Weights Store       → Gradient-ascent learning → update weights.json
```

---

## Stage-by-Stage Breakdown

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
- **Sentiment:** Weighted keyword scoring over news headlines. Positive and negative word lists have per-word weights (0.5–2.0). Threshold for classification: 0.05 normalized per headline.
- **Volume Signal:** `(current_vol - avg_vol_20) / avg_vol_20`, clipped to `±2.0`

**Output:**
```python
{
  "trend": "uptrend" | "downtrend",
  "support": [95.50, 96.20, ...],
  "resistance": [105.30, 106.10, ...],
  "volatility": 1.25,          # ATR
  "sentiment": "positive" | "negative" | "neutral",
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

**Input:** Analysis signals + pattern output

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

### 5. Risk Engine — `backend/utils/risk_engine.py`

**Input:** Decision output + analysis signals

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
  - Returns `executed=True` with `trade_id`

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

### 7. Feedback Agent — `backend/agents/feedback_agent.py`

**Input:** Current price for a previously executed trade

**What it does:**
- For BUY trades:
  - `current_price >= target` → `result = "correct"`
  - `current_price <= stop_loss` → `result = "wrong"`
  - Otherwise → `result = "pending"`
- For SELL trades: logic is reversed.
- Updates the trade record in `memory.json` with the result.

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

## Execution Modes

### On-Demand (API)
```
GET http://localhost:5001/analyze?symbol=RELIANCE.NS
```
Runs the full pipeline (Stages 1–6) for one symbol and returns a JSON response.

### Automated (Scheduler)
`backend/scheduler.py` runs the pipeline every 15 minutes for all symbols defined in `config.py`. Runs once immediately on startup, then on schedule.

### One-Command Startup
```bash
./run.sh
```
Starts the Flask backend on port 5001 and the frontend on port 8080.

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
| `backend/services/stock_service.py` | yfinance / Alpha Vantage fetcher |
| `backend/services/news_service.py` | Google Finance RSS / NewsAPI fetcher |
| `backend/scheduler.py` | Automated 15-min scheduler |
| `frontend/script.js` | Web UI — calls /analyze and renders results |
