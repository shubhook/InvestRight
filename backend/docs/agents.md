# Agent Specifications

## Data Agent
**Purpose:** Fetch and package raw market data
**Input:** Stock symbol (string)
**Output:** 
```json
{
  "symbol": "RELIANCE.NS",
  "ohlc": DataFrame with columns [open, high, low, close],
  "volume": Series,
  "news": ["Headline 1", "Headline 2", ...]
}
```
**Failure Cases:** API failures, invalid symbols, missing data
**Example:** Fetch RELIANCE.NS OHLCV data + latest news headlines

## Analysis Agent
**Purpose:** Extract structured signals from raw data
**Input:** Data Agent output
**Output:**
```json
{
  "trend": "uptrend" or "downtrend",
  "support": [95.50, 96.20, ...],
  "resistance": [105.30, 106.10, ...],
  "volatility": 1.25,
  "sentiment": "positive" or "negative" or "neutral"
}
```
**Failure Cases:** Insufficient data, calculation errors
**Example:** Calculate SMA-20/SMA-50 for trend, find local minima/maxima for S/R, ATR for volatility, keyword scoring for sentiment

## Decision Agent
**Purpose:** Make trading decisions based on analysis and patterns
**Input:** Analysis Agent output + Pattern Engine output
**Output:**
```json
{
  "action": "BUY" or "SELL" or "WAIT",
  "reason": "Pattern: ascending_triangle, Trend: uptrend",
  "confidence": 0.85,
  "sentiment_flag": "positive" or null
}
```
**Logic:**
- IF pattern confidence < 0.6 → WAIT
- IF pattern bullish AND trend up → BUY
- IF pattern bearish AND trend down → SELL
- ELSE → WAIT
**Failure Cases:** Missing inputs, calculation errors

## Action Agent
**Purpose:** Execute decisions and store trades
**Input:** Risk Engine output
**Output:**
```json
{
  "executed": true,
  "trade_id": "uuid-string",
  "reason": "Trade executed: BUY",
  "trade_record": { ... }
}
```
**Responsibilities:**
- Log decision to console and file
- Generate trade ID
- Store trade record in memory
**Failure Cases:** Storage failures, invalid decision data

## Feedback Agent
**Purpose:** Evaluate trade outcomes and learn from results
**Input:** Trade ID + Current market price
**Output:**
```json
{
  "trade_id": "uuid-string",
  "result": "correct" or "wrong" or "pending",
  "message": "BUY trade hit target: 108.50 >= 108.00"
}
```
**Logic:**
- For BUY: correct if price >= target, wrong if price <= stop_loss
- For SELL: correct if price <= target, wrong if price >= stop_loss
- Store result back to memory
**Failure Cases:** Missing trade data, storage errors