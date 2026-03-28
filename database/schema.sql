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

CREATE TABLE IF NOT EXISTS positions (
    position_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    trade_id            UUID REFERENCES trades(trade_id),
    quantity            NUMERIC(12, 4),
    avg_entry           NUMERIC(12, 4),
    current_price       NUMERIC(12, 4),
    unrealised_pnl      NUMERIC(12, 4),
    status              VARCHAR(10) CHECK (status IN ('open', 'closed')),
    opened_at           TIMESTAMPTZ DEFAULT NOW(),
    closed_at           TIMESTAMPTZ
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

-- Capital account — single source of truth for money
CREATE TABLE IF NOT EXISTS capital_account (
    id                  SERIAL PRIMARY KEY,
    total_capital       NUMERIC(15, 2) NOT NULL,
    deployed_capital    NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    available_capital   NUMERIC(15, 2) NOT NULL,
    realised_pnl        NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Drop and recreate positions with full schema (replaces Batch 1 placeholder)
DROP TABLE IF EXISTS positions CASCADE;

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
