CREATE SCHEMA IF NOT EXISTS master;
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS master.security (
    id                  BIGSERIAL PRIMARY KEY,
    security_id         VARCHAR(32) NOT NULL UNIQUE,
    code                VARCHAR(16) NOT NULL UNIQUE,
    symbol              VARCHAR(16) NOT NULL,
    exchange_code       VARCHAR(16) NOT NULL,
    security_name       VARCHAR(100) NOT NULL,
    company_name        VARCHAR(200),
    security_name_en    VARCHAR(200),
    company_name_en     VARCHAR(200),
    pinyin              VARCHAR(100),
    security_type       VARCHAR(32) NOT NULL DEFAULT 'STOCK',
    board_type          VARCHAR(32),
    list_plate          VARCHAR(100),
    industry            VARCHAR(100),
    currency_code       VARCHAR(8) NOT NULL DEFAULT 'CNY',
    list_date           DATE,
    delist_date         DATE,
    listing_status      VARCHAR(32) NOT NULL DEFAULT 'LISTED',
    is_st               BOOLEAN NOT NULL DEFAULT FALSE,
    is_connect          BOOLEAN NOT NULL DEFAULT FALSE,
    lot_size            INTEGER NOT NULL DEFAULT 100,
    price_tick          NUMERIC(12, 4) NOT NULL DEFAULT 0.01,
    source_code         VARCHAR(32) NOT NULL DEFAULT 'AMAZINGDATA',
    source_updated_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_security_exchange
        CHECK (exchange_code IN ('SSE', 'SZSE', 'BSE')),
    CONSTRAINT ck_security_listing_status
        CHECK (listing_status IN ('LISTED', 'SUSPENDED', 'DELISTING', 'DELISTED'))
);

CREATE INDEX IF NOT EXISTS idx_security_symbol
    ON master.security (symbol);
CREATE INDEX IF NOT EXISTS idx_security_name
    ON master.security (security_name);
CREATE INDEX IF NOT EXISTS idx_security_exchange_status
    ON master.security (exchange_code, listing_status);
CREATE INDEX IF NOT EXISTS idx_security_board
    ON master.security (board_type);

CREATE TABLE IF NOT EXISTS market.trade_calendar (
    trade_date          DATE NOT NULL,
    market              VARCHAR(16) NOT NULL,
    is_trading_day      BOOLEAN NOT NULL,
    weekday             SMALLINT NOT NULL,
    source_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market, trade_date)
);

CREATE TABLE IF NOT EXISTS market.daily_bar (
    security_id         BIGINT NOT NULL REFERENCES master.security(id),
    trade_date          DATE NOT NULL,
    open_price          NUMERIC(18, 4),
    high_price          NUMERIC(18, 4),
    low_price           NUMERIC(18, 4),
    close_price         NUMERIC(18, 4),
    volume              NUMERIC(24, 4),
    amount              NUMERIC(24, 4),
    adj_factor          NUMERIC(24, 10),
    is_suspended        BOOLEAN NOT NULL DEFAULT FALSE,
    source_code         VARCHAR(32) NOT NULL DEFAULT 'AMAZINGDATA',
    source_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (security_id, trade_date),
    CONSTRAINT ck_daily_bar_volume CHECK (volume IS NULL OR volume >= 0),
    CONSTRAINT ck_daily_bar_amount CHECK (amount IS NULL OR amount >= 0)
);

CREATE TABLE IF NOT EXISTS market.weekly_bar (
    security_id         BIGINT NOT NULL REFERENCES master.security(id),
    trade_date          DATE NOT NULL,
    period_start_date   DATE,
    period_end_date     DATE,
    open_price          NUMERIC(18, 4),
    high_price          NUMERIC(18, 4),
    low_price           NUMERIC(18, 4),
    close_price         NUMERIC(18, 4),
    volume              NUMERIC(24, 4),
    amount              NUMERIC(24, 4),
    adj_factor          NUMERIC(24, 10),
    is_suspended        BOOLEAN NOT NULL DEFAULT FALSE,
    source_code         VARCHAR(32) NOT NULL DEFAULT 'AMAZINGDATA',
    source_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (security_id, trade_date)
);

CREATE TABLE IF NOT EXISTS market.monthly_bar (
    security_id         BIGINT NOT NULL REFERENCES master.security(id),
    trade_date          DATE NOT NULL,
    period_start_date   DATE,
    period_end_date     DATE,
    open_price          NUMERIC(18, 4),
    high_price          NUMERIC(18, 4),
    low_price           NUMERIC(18, 4),
    close_price         NUMERIC(18, 4),
    volume              NUMERIC(24, 4),
    amount              NUMERIC(24, 4),
    adj_factor          NUMERIC(24, 10),
    is_suspended        BOOLEAN NOT NULL DEFAULT FALSE,
    source_code         VARCHAR(32) NOT NULL DEFAULT 'AMAZINGDATA',
    source_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (security_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_bar_trade_date
    ON market.daily_bar (trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_bar_security_date_desc
    ON market.daily_bar (security_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_weekly_bar_trade_date
    ON market.weekly_bar (trade_date);
CREATE INDEX IF NOT EXISTS idx_weekly_bar_security_date_desc
    ON market.weekly_bar (security_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_monthly_bar_trade_date
    ON market.monthly_bar (trade_date);
CREATE INDEX IF NOT EXISTS idx_monthly_bar_security_date_desc
    ON market.monthly_bar (security_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS market.sync_job (
    id                  BIGSERIAL PRIMARY KEY,
    job_name            VARCHAR(100) NOT NULL,
    data_type           VARCHAR(32) NOT NULL,
    source_code         VARCHAR(32) NOT NULL DEFAULT 'AMAZINGDATA',
    sync_mode           VARCHAR(32) NOT NULL,
    job_status          VARCHAR(32) NOT NULL,
    range_start         DATE,
    range_end           DATE,
    records_read        INTEGER NOT NULL DEFAULT 0,
    records_inserted    INTEGER NOT NULL DEFAULT 0,
    records_updated     INTEGER NOT NULL DEFAULT 0,
    records_failed      INTEGER NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_job_type_started
    ON market.sync_job (data_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_job_status
    ON market.sync_job (job_status, started_at DESC);

-- Less frequently queried AmazingData datasets are kept as typed metadata
-- plus JSONB. This avoids falling back to Parquet while preserving all vendor
-- columns until dedicated relational models are introduced.
CREATE TABLE IF NOT EXISTS market.reference_data (
    data_type           VARCHAR(64) NOT NULL,
    natural_key         VARCHAR(64) NOT NULL,
    code                VARCHAR(32),
    reference_date      DATE,
    payload             JSONB NOT NULL,
    source_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (data_type, natural_key)
);

CREATE INDEX IF NOT EXISTS idx_reference_data_code_date
    ON market.reference_data (data_type, code, reference_date);

-- Compatibility views keep the public /historical/sql contract stable.
CREATE OR REPLACE VIEW market.v_kline_day AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.daily_bar b
JOIN master.security s ON s.id = b.security_id;

CREATE OR REPLACE VIEW market.v_kline_week AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.weekly_bar b
JOIN master.security s ON s.id = b.security_id;

CREATE OR REPLACE VIEW market.v_kline_month AS
SELECT s.code,
       TO_CHAR(b.trade_date, 'YYYYMMDD')::INTEGER AS date,
       b.open_price AS open, b.high_price AS high, b.low_price AS low,
       b.close_price AS close, b.volume, b.amount, b.adj_factor,
       b.is_suspended, EXTRACT(EPOCH FROM b.source_updated_at)::BIGINT AS sync_at
FROM market.monthly_bar b
JOIN master.security s ON s.id = b.security_id;
