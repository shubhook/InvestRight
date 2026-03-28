# System Design

## First Principles

1. **Patterns are Geometric, Not Predictive**: Chart patterns represent recurring geometric structures in price movement. They are not predictive in isolation but provide actionable signals when combined with trend and risk constraints.

2. **Risk Control is Paramount**: The primary goal is not to win every trade but to ensure that losses are controlled (max 2% per trade) and winners significantly outweigh losers (minimum 2:1 reward-to-risk ratio).

3. **Explainability Over Complexity**: Every decision must be traceable to a specific rule. No black-box logic is permitted.

4. **Feedback-Driven Improvement**: The system learns from its mistakes by recording outcomes and adjusting future behavior (e.g., avoiding patterns with high failure rates).

## Pipeline Reasoning

### Step 1: Data → Structure
- **Purpose**: Transform raw market data into a structured format.
- **Why**: Raw OHLCV and news data must be cleaned, validated, and packaged for downstream processing.
- **Output**: A reliable data package containing symbol, OHLCV, volume, and news headlines.

### Step 2: Structure → Signals
- **Purpose**: Extract meaningful signals from structured data.
- **Why**: Raw data is noisy. We compute trend (SMA crossover), support/resistance (local minima/maxima), volatility (ATR), and sentiment (keyword scoring) to distill actionable information.
- **Output**: A signal set that summarizes the market context.

### Step 3: Signals → Pattern
- **Purpose**: Identify chart patterns that, combined with signals, suggest a directional bias.
- **Why**: Patterns like double tops or ascending triangles, when aligned with the trend, increase the probability of a successful trade.
- **Output**: A pattern detection result with confidence and direction.

### Step 4: Pattern + Signals → Decision
- **Purpose**: Combine pattern and signals into a trading bias (BUY/SELL/WAIT).
- **Why**: A pattern alone is insufficient. We require alignment with the trend (e.g., bullish pattern in uptrend) and sufficient confidence (≥0.6).
- **Output**: A directional bias with reasoning.

### Step 5: Decision → Risk-Adjusted Action
- **Purpose**: Apply risk management to the decision.
- **Why**: Even a high-confidence signal can lead to ruin without proper risk controls. We calculate stop loss (nearest support/resistance), target (2:1 reward-to-risk), and validate against max loss (2%).
- **Output**: A risk-adjusted action (BUY/SELL/WAIT) with exact entry, stop, target, and risk metrics.

### Step 6: Action → Execution
- **Purpose**: Record the trade for tracking and evaluation.
- **Why**: We cannot learn without recording what we did. Each trade is logged with its ID, parameters, and timestamp.
- **Output**: A trade record stored in memory.

### Step 7: Execution → Feedback
- **Purpose**: Evaluate past trades against actual market outcomes.
- **Why**: Learning requires feedback. We check if a trade hit its target or stop loss and record the result.
- **Output**: A trade outcome (correct/wrong/pending) that updates memory.

## Failure Modes and Mitigations

1. **Overfitting Patterns**
   - **Risk**: Seeing patterns in noise, leading to false signals.
   - **Mitigation**: Require multiple conditions (e.g., for double top: two peaks within 2%, valley 3% below, volume distribution) and set a high confidence threshold (0.6).

2. **Ignoring Volume**
   - **Risk**: Price patterns without volume confirmation may fail.
   - **Mitigation**: In double top, we require lower volume on the second peak (distribution). Other patterns could be enhanced with volume checks in future iterations.

3. **Poor Risk Control**
   - **Risk**: Losses exceeding 2% or reward-to-risk ratio below 2:1.
   - **Mitigation**: The risk engine is non-negotiable and runs on every decision. It calculates stop loss from support/resistance, enforces 2:1 RR, and caps loss at 2%.

4. **Data Failures**
   - **Risk**: API outages or invalid data causing pipeline crashes.
   - **Mitigation**: Every module handles exceptions and returns safe defaults (e.g., empty data, WAIT decision). The pipeline logs errors and halts cleanly.

5. **Look-Ahead Bias**
   - **Risk**: Using future data in pattern detection or analysis.
   - **Mitigation**: All calculations use only historical data up to the current candle. No future data is accessed.

## Component Interactions

- **Data Agent** feeds raw data to **Analysis Agent**.
- **Analysis Agent** provides trend, support/resistance, volatility, and sentiment to **Decision Agent** and **Risk Engine**.
- **Pattern Engine** receives OHLCV data and outputs pattern, confidence, and direction to **Decision Agent**.
- **Decision Agent** combines analysis and pattern to produce a bias, which goes to **Risk Engine**.
- **Risk Engine** validates and refines the decision, outputting a trade plan to **Action Agent**.
- **Action Agent** stores the trade plan in memory via **Memory Store**.
- **Feedback Agent** uses **Memory Reader** to retrieve past trades and update their results based on current prices.
- **Memory Store** and **Memory Reader** form a persistent ledger of trades and outcomes.

## Extensibility

- **New Patterns**: Add a new detection function in `pattern_engine.py` and register it in the `detect_pattern` function.
- **New Signals**: Extend `analysis_agent.py` with additional indicators (e.g., RSI, MACD).
- **New Risk Rules**: Modify `risk_engine.py` to adjust stop loss/target logic or add new filters (e.g., max daily trades).
- **New Data Sources**: Update `stock_service.py` and `news_service.py` to incorporate additional APIs.

## Conclusion

This system is designed to be a transparent, rule-based trading assistant that prioritizes capital preservation and learning from experience. By combining geometric pattern recognition with strict risk controls and a feedback loop, it aims to improve trading discipline over time.