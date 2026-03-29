-- Migration 001: Add watchlist table
-- Run this if you already have an existing database set up before this change.
-- Safe to run multiple times (idempotent).

CREATE TABLE IF NOT EXISTS watchlist (
    symbol          VARCHAR(20) PRIMARY KEY,
    capital_pct     NUMERIC(5, 2) NOT NULL DEFAULT 10.00
                    CHECK (capital_pct > 0 AND capital_pct <= 100),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
