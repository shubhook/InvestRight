CREATE TABLE IF NOT EXISTS trades (
    trade_id            UUID PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL,
    symbol              VARCHAR(20) NOT NULL,
    action              VARCHAR(10) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    entry               NUMERIC(12, 4),
    stop_loss           NUMERIC(12, 4),
    target              NUMERIC(12, 4),
    rr_ratio            NUMERIC(6, 4),
    max_loss_pct        NUMERIC(6, 4),
    position_size_fraction NUMERIC(6, 4),
    features_vector     JSONB,
    result              VARCHAR(10) CHECK (result IN ('correct', 'wrong', 'pending')),
    rejection_reason    TEXT,
    updated_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS weights (
    id                  SERIAL PRIMARY KEY,
    w_bias              NUMERIC(10, 6) NOT NULL,
    w_trend             NUMERIC(10, 6) NOT NULL,
    w_sentiment         NUMERIC(10, 6) NOT NULL,
    w_pattern           NUMERIC(10, 6) NOT NULL,
    w_volatility        NUMERIC(10, 6) NOT NULL,
    w_sr_signal         NUMERIC(10, 6) NOT NULL,
    w_volume            NUMERIC(10, 6) NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    action              VARCHAR(10) NOT NULL,
    confidence          NUMERIC(10, 6),
    probability_up      NUMERIC(10, 6),
    expected_value      NUMERIC(12, 4),
    pattern             VARCHAR(50),
    pattern_confidence  NUMERIC(6, 4),
    trend               VARCHAR(20),
    sentiment           VARCHAR(20),
    volume_signal       NUMERIC(10, 6),
    volatility          NUMERIC(12, 4),
    reason              TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Kill switch state
CREATE TABLE IF NOT EXISTS kill_switch (
    id              SERIAL PRIMARY KEY,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    reason          TEXT,
    activated_by    VARCHAR(100),
    activated_at    TIMESTAMPTZ DEFAULT NOW(),
    deactivated_at  TIMESTAMPTZ
);

-- Insert default row (trading enabled) if table is empty
INSERT INTO kill_switch (is_active, reason, activated_by)
SELECT FALSE, 'system_init', 'system'
WHERE NOT EXISTS (SELECT 1 FROM kill_switch);

-- Idempotency log
CREATE TABLE IF NOT EXISTS idempotency_log (
    idempotency_key VARCHAR(100) PRIMARY KEY,
    trade_id        UUID REFERENCES trades(trade_id),
    symbol          VARCHAR(20) NOT NULL,
    action          VARCHAR(10) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Per-symbol capital limits
CREATE TABLE IF NOT EXISTS capital_limits (
    symbol               VARCHAR(20) PRIMARY KEY,
    max_capital_pct      NUMERIC(5, 2) NOT NULL DEFAULT 10.00,
    current_exposure_pct NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- Order state machine
CREATE TABLE IF NOT EXISTS orders (
    order_id            VARCHAR(50) PRIMARY KEY,
    trade_id            UUID REFERENCES trades(trade_id),
    symbol              VARCHAR(20) NOT NULL,
    action              VARCHAR(10) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    order_type          VARCHAR(20) NOT NULL DEFAULT 'MARKET',
    quantity            INTEGER NOT NULL,
    price               NUMERIC(12, 4),
    trigger_price       NUMERIC(12, 4),
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING', 'PLACED', 'FILLED', 'PARTIAL',
                            'CANCELLED', 'FAILED', 'REJECTED'
                        )),
    filled_quantity     INTEGER DEFAULT 0,
    filled_price        NUMERIC(12, 4),
    broker_order_id     VARCHAR(100),
    broker_mode         VARCHAR(10) NOT NULL DEFAULT 'paper'
                        CHECK (broker_mode IN ('paper', 'live')),
    failure_reason      TEXT,
    retry_count         INTEGER DEFAULT 0,
    placed_at           TIMESTAMPTZ,
    filled_at           TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_trade_id ON orders(trade_id);
CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol   ON orders(symbol);

-- Capital account — single source of truth for money.
-- Exactly one row must exist at all times.
-- deploy_capital() and release_capital() use atomic UPDATE (not INSERT).
-- Only capital_account.initialise() may INSERT the seed row.
CREATE TABLE IF NOT EXISTS capital_account (
    id                  SERIAL PRIMARY KEY,
    total_capital       NUMERIC(15, 2) NOT NULL,
    deployed_capital    NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    available_capital   NUMERIC(15, 2) NOT NULL,
    realised_pnl        NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
-- Enforce single-row invariant: unique index on a constant expression.
-- This will fail loudly if a second row is ever inserted accidentally.
CREATE UNIQUE INDEX IF NOT EXISTS capital_account_single_row ON capital_account ((1));

-- Full positions schema — safe to run multiple times (CREATE IF NOT EXISTS)
CREATE TABLE IF NOT EXISTS positions (
    position_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id            UUID REFERENCES trades(trade_id),
    order_id            VARCHAR(50) REFERENCES orders(order_id),
    symbol              VARCHAR(20) NOT NULL,
    action              VARCHAR(10) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    quantity            INTEGER NOT NULL,
    entry_price         NUMERIC(12, 4) NOT NULL,
    current_price       NUMERIC(12, 4),
    stop_loss           NUMERIC(12, 4) NOT NULL,
    target              NUMERIC(12, 4) NOT NULL,
    unrealised_pnl      NUMERIC(15, 2) DEFAULT 0.00,
    realised_pnl        NUMERIC(15, 2),
    exit_price          NUMERIC(12, 4),
    exit_reason         VARCHAR(50),
    status              VARCHAR(10) NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed')),
    capital_deployed    NUMERIC(15, 2) NOT NULL,
    opened_at           TIMESTAMPTZ DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol   ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status   ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_trade_id ON positions(trade_id);

-- P&L snapshots — daily portfolio valuation history
CREATE TABLE IF NOT EXISTS pnl_snapshots (
    snapshot_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date    DATE NOT NULL,
    total_capital    NUMERIC(15, 2) NOT NULL,
    deployed_capital NUMERIC(15, 2) NOT NULL,
    available_capital NUMERIC(15, 2) NOT NULL,
    unrealised_pnl   NUMERIC(15, 2) NOT NULL,
    realised_pnl     NUMERIC(15, 2) NOT NULL,
    open_positions   INTEGER NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (snapshot_date)
);

-- Backtesting tables — isolated from live tables
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    interval        VARCHAR(20) NOT NULL DEFAULT '15m',
    initial_capital NUMERIC(15, 2) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed')),
    metrics         JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id              SERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES backtest_runs(run_id) ON DELETE CASCADE,
    bar_index       INTEGER NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    action          VARCHAR(10) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    entry_price     NUMERIC(12, 4) NOT NULL,
    exit_price      NUMERIC(12, 4),
    stop_loss       NUMERIC(12, 4),
    target          NUMERIC(12, 4),
    quantity        INTEGER,
    pnl             NUMERIC(15, 2),
    exit_reason     VARCHAR(20),
    result          VARCHAR(10) CHECK (result IN ('correct', 'wrong', 'pending')),
    entry_bar_time  TIMESTAMPTZ,
    exit_bar_time   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_run_id ON backtest_trades(run_id);

CREATE TABLE IF NOT EXISTS backtest_equity_curve (
    id              SERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES backtest_runs(run_id) ON DELETE CASCADE,
    bar_index       INTEGER NOT NULL,
    bar_time        TIMESTAMPTZ,
    equity          NUMERIC(15, 2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bt_equity_run_id ON backtest_equity_curve(run_id);

-- Observability tables

-- Structured audit log — every pipeline event
CREATE TABLE IF NOT EXISTS audit_log (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id        UUID NOT NULL,
    span_id         UUID NOT NULL DEFAULT gen_random_uuid(),
    event_type      VARCHAR(50) NOT NULL,
    component       VARCHAR(50) NOT NULL,
    symbol          VARCHAR(20),
    trade_id        UUID,
    severity        VARCHAR(10) NOT NULL DEFAULT 'INFO'
                    CHECK (severity IN (
                        'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
                    )),
    message         TEXT NOT NULL,
    metadata        JSONB,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_trace_id   ON audit_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_symbol     ON audit_log(symbol);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);

-- Pipeline latency metrics per component
CREATE TABLE IF NOT EXISTS pipeline_metrics (
    metric_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id        UUID NOT NULL,
    component       VARCHAR(50) NOT NULL,
    symbol          VARCHAR(20),
    duration_ms     INTEGER NOT NULL,
    status          VARCHAR(10) NOT NULL
                    CHECK (status IN ('success', 'failure')),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_component ON pipeline_metrics(component);

-- Model performance tracking
CREATE TABLE IF NOT EXISTS model_performance (
    id                  SERIAL PRIMARY KEY,
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    total_predictions   INTEGER NOT NULL DEFAULT 0,
    correct_predictions INTEGER NOT NULL DEFAULT 0,
    accuracy            NUMERIC(6, 4),
    brier_score         NUMERIC(8, 6),
    avg_confidence      NUMERIC(6, 4),
    calibration_error   NUMERIC(8, 6),
    weights_snapshot    JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- LLM call log — track every Anthropic API call
-- Missing indexes from previous batches
CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_result     ON trades(result);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp  ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_severity ON audit_log(severity);

-- Rate limit tracking
CREATE TABLE IF NOT EXISTS rate_limit_log (
    id            SERIAL PRIMARY KEY,
    endpoint      VARCHAR(100) NOT NULL,
    client_id     VARCHAR(100) NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    window_start  TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_client ON rate_limit_log(client_id, window_start);

-- Kite token management
CREATE TABLE IF NOT EXISTS kite_tokens (
    id            SERIAL PRIMARY KEY,
    access_token  TEXT NOT NULL,
    request_token TEXT,
    valid_from    TIMESTAMPTZ NOT NULL,
    valid_until   TIMESTAMPTZ NOT NULL,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Watchlist — user-selected symbols for autonomous trading
CREATE TABLE IF NOT EXISTS watchlist (
    symbol          VARCHAR(20) PRIMARY KEY,
    capital_pct     NUMERIC(5, 2) NOT NULL DEFAULT 10.00
                    CHECK (capital_pct > 0 AND capital_pct <= 100),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id          UUID,
    agent             VARCHAR(50) NOT NULL,
    model             VARCHAR(50) NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    latency_ms        INTEGER,
    status            VARCHAR(10) NOT NULL
                      CHECK (status IN ('success', 'failure')),
    fallback_used     BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
