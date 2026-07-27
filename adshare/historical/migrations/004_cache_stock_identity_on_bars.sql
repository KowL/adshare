DO $$
DECLARE
    target_table TEXT;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['daily_bar', 'weekly_bar', 'monthly_bar']
    LOOP
        EXECUTE format(
            'ALTER TABLE market.%I ADD COLUMN IF NOT EXISTS stock_code VARCHAR(16)',
            target_table
        );
        EXECUTE format(
            'ALTER TABLE market.%I ADD COLUMN IF NOT EXISTS stock_name VARCHAR(100)',
            target_table
        );
        EXECUTE format(
            'UPDATE market.%1$I b '
            'SET stock_code = s.code, stock_name = s.security_name, updated_at = NOW() '
            'FROM master.stock s '
            'WHERE s.stock_id = b.stock_id '
            'AND (b.stock_code IS DISTINCT FROM s.code '
            'OR b.stock_name IS DISTINCT FROM s.security_name)',
            target_table
        );
        EXECUTE format(
            'ALTER TABLE market.%I ALTER COLUMN stock_code SET NOT NULL',
            target_table
        );
        EXECUTE format(
            'ALTER TABLE market.%I ALTER COLUMN stock_name SET NOT NULL',
            target_table
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS idx_%1$s_stock_code_date_desc '
            'ON market.%1$I (stock_code, trade_date DESC)',
            target_table
        );
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION master.propagate_stock_identity_cache()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE market.daily_bar
       SET stock_code = NEW.code,
           stock_name = NEW.security_name,
           updated_at = NOW()
     WHERE stock_id = NEW.stock_id
       AND (stock_code IS DISTINCT FROM NEW.code
            OR stock_name IS DISTINCT FROM NEW.security_name);

    UPDATE market.weekly_bar
       SET stock_code = NEW.code,
           stock_name = NEW.security_name,
           updated_at = NOW()
     WHERE stock_id = NEW.stock_id
       AND (stock_code IS DISTINCT FROM NEW.code
            OR stock_name IS DISTINCT FROM NEW.security_name);

    UPDATE market.monthly_bar
       SET stock_code = NEW.code,
           stock_name = NEW.security_name,
           updated_at = NOW()
     WHERE stock_id = NEW.stock_id
       AND (stock_code IS DISTINCT FROM NEW.code
            OR stock_name IS DISTINCT FROM NEW.security_name);

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_stock_identity_cache ON master.stock;
CREATE TRIGGER trg_stock_identity_cache
AFTER UPDATE OF code, security_name ON master.stock
FOR EACH ROW
WHEN (
    OLD.code IS DISTINCT FROM NEW.code
    OR OLD.security_name IS DISTINCT FROM NEW.security_name
)
EXECUTE FUNCTION master.propagate_stock_identity_cache();

DROP VIEW IF EXISTS market.v_kline_day;
DROP VIEW IF EXISTS market.v_kline_week;
DROP VIEW IF EXISTS market.v_kline_month;

CREATE VIEW market.v_kline_day AS
SELECT b.stock_code AS code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at,
       b.stock_name AS name
FROM market.daily_bar b
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;

CREATE VIEW market.v_kline_week AS
SELECT b.stock_code AS code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at,
       b.stock_name AS name
FROM market.weekly_bar b
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;

CREATE VIEW market.v_kline_month AS
SELECT b.stock_code AS code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, af.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at,
       b.stock_name AS name
FROM market.monthly_bar b
LEFT JOIN LATERAL (
    SELECT f.adj_factor
    FROM market.adjustment_factor f
    WHERE f.stock_id = b.stock_id
      AND f.effective_date <= b.trade_date
    ORDER BY f.effective_date DESC
    LIMIT 1
) af ON TRUE;
