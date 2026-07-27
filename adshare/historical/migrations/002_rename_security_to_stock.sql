DO $$
BEGIN
    IF to_regclass('master.security') IS NOT NULL
       AND to_regclass('master.stock') IS NULL THEN
        ALTER TABLE master.security RENAME TO stock;
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'master'
          AND table_name = 'stock'
          AND column_name = 'id'
    ) THEN
        ALTER TABLE master.stock RENAME COLUMN id TO stock_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'master'
          AND table_name = 'stock'
          AND column_name = 'security_id'
    ) THEN
        ALTER TABLE master.stock RENAME COLUMN security_id TO stock_key;
    END IF;
END
$$;

DO $$
DECLARE
    target_table TEXT;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['daily_bar', 'weekly_bar', 'monthly_bar']
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'market'
              AND information_schema.columns.table_name = target_table
              AND column_name = 'security_id'
        ) THEN
            EXECUTE format(
                'ALTER TABLE market.%I RENAME COLUMN security_id TO stock_id',
                target_table
            );
        END IF;
    END LOOP;
END
$$;

ALTER INDEX IF EXISTS master.idx_security_symbol
    RENAME TO idx_stock_symbol;
ALTER INDEX IF EXISTS master.idx_security_name
    RENAME TO idx_stock_name;
ALTER INDEX IF EXISTS master.idx_security_exchange_status
    RENAME TO idx_stock_exchange_status;
ALTER INDEX IF EXISTS master.idx_security_board
    RENAME TO idx_stock_board;

ALTER INDEX IF EXISTS market.idx_daily_bar_security_date_desc
    RENAME TO idx_daily_bar_stock_date_desc;
ALTER INDEX IF EXISTS market.idx_weekly_bar_security_date_desc
    RENAME TO idx_weekly_bar_stock_date_desc;
ALTER INDEX IF EXISTS market.idx_monthly_bar_security_date_desc
    RENAME TO idx_monthly_bar_stock_date_desc;

DO $$
DECLARE
    rename_pair TEXT[];
BEGIN
    FOREACH rename_pair SLICE 1 IN ARRAY ARRAY[
        ['security_pkey', 'stock_pkey'],
        ['uk_security_security_id', 'uk_stock_stock_key'],
        ['uk_security_exchange_symbol', 'uk_stock_exchange_symbol'],
        ['ck_security_exchange', 'ck_stock_exchange'],
        ['ck_security_listing_status', 'ck_stock_listing_status']
    ]
    LOOP
        IF EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE connamespace = 'master'::regnamespace
              AND conname = rename_pair[1]
        ) THEN
            EXECUTE format(
                'ALTER TABLE master.stock RENAME CONSTRAINT %I TO %I',
                rename_pair[1],
                rename_pair[2]
            );
        END IF;
    END LOOP;
END
$$;

DO $$
DECLARE
    target_table TEXT;
    old_constraint TEXT;
    new_constraint TEXT;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['daily_bar', 'weekly_bar', 'monthly_bar']
    LOOP
        old_constraint := target_table || '_security_id_fkey';
        new_constraint := target_table || '_stock_id_fkey';
        IF EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE connamespace = 'market'::regnamespace
              AND conname = old_constraint
        ) THEN
            EXECUTE format(
                'ALTER TABLE market.%I RENAME CONSTRAINT %I TO %I',
                target_table,
                old_constraint,
                new_constraint
            );
        END IF;
    END LOOP;
END
$$;

CREATE OR REPLACE VIEW market.v_kline_day AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.daily_bar b
JOIN master.stock s ON s.stock_id = b.stock_id;

CREATE OR REPLACE VIEW market.v_kline_week AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.weekly_bar b
JOIN master.stock s ON s.stock_id = b.stock_id;

CREATE OR REPLACE VIEW market.v_kline_month AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.monthly_bar b
JOIN master.stock s ON s.stock_id = b.stock_id;
