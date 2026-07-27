CREATE TABLE IF NOT EXISTS market.adjustment_factor (
    stock_id           BIGINT NOT NULL
                       REFERENCES master.stock(stock_id) ON DELETE CASCADE,
    effective_date     DATE NOT NULL,
    adj_factor         NUMERIC(24, 10) NOT NULL,
    source_updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_id, effective_date),
    CONSTRAINT ck_adjustment_factor_positive CHECK (adj_factor > 0)
);

CREATE INDEX IF NOT EXISTS idx_adjustment_factor_stock_date_desc
    ON market.adjustment_factor (stock_id, effective_date DESC);

-- Preserve factors already written by deployments using the denormalized
-- bar-table columns. Daily factors are the canonical source; week/month
-- values were derived from the same series.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'market'
          AND table_name = 'daily_bar'
          AND column_name = 'adj_factor'
    ) THEN
        INSERT INTO market.adjustment_factor (
            stock_id, effective_date, adj_factor, source_updated_at
        )
        SELECT stock_id, trade_date, adj_factor, source_updated_at
        FROM market.daily_bar
        WHERE adj_factor IS NOT NULL
          AND adj_factor > 0
        ON CONFLICT (stock_id, effective_date) DO UPDATE SET
            adj_factor = EXCLUDED.adj_factor,
            source_updated_at = EXCLUDED.source_updated_at,
            updated_at = NOW();
    END IF;
END
$$;

DROP VIEW IF EXISTS market.v_kline_day;
DROP VIEW IF EXISTS market.v_kline_week;
DROP VIEW IF EXISTS market.v_kline_month;

ALTER TABLE market.daily_bar DROP COLUMN IF EXISTS adj_factor;
ALTER TABLE market.weekly_bar DROP COLUMN IF EXISTS adj_factor;
ALTER TABLE market.monthly_bar DROP COLUMN IF EXISTS adj_factor;

CREATE OR REPLACE VIEW market.v_kline_day AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.daily_bar b
JOIN master.stock s ON s.stock_id = b.stock_id
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;

CREATE OR REPLACE VIEW market.v_kline_week AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.weekly_bar b
JOIN master.stock s ON s.stock_id = b.stock_id
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;

CREATE OR REPLACE VIEW market.v_kline_month AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.monthly_bar b
JOIN master.stock s ON s.stock_id = b.stock_id
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;
