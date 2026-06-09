-- DuckDB Schema for Market Data Warehouse
-- Three grain sizes for OHLCV + corporate actions + ticker reference

-- Hourly bars (1 bar per trading hour per ticker)
CREATE TABLE IF NOT EXISTS hourly_bars (
    ticker VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,          -- Unix ms, start of bar
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,                       -- split-adjusted
    vwap DOUBLE,
    transactions INTEGER,
    PRIMARY KEY (ticker, timestamp)
);

-- Daily bars (1 bar per trading day per ticker)
CREATE TABLE IF NOT EXISTS daily_bars (
    ticker VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    vwap DOUBLE,
    transactions INTEGER,
    PRIMARY KEY (ticker, timestamp)
);

-- Weekly bars (1 bar per week per ticker)
CREATE TABLE IF NOT EXISTS weekly_bars (
    ticker VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    vwap DOUBLE,
    transactions INTEGER,
    PRIMARY KEY (ticker, timestamp)
);

-- Dividend history (event-based, one row per declared dividend)
CREATE TABLE IF NOT EXISTS dividends (
    ticker VARCHAR(10) NOT NULL,
    ex_dividend_date DATE NOT NULL,
    cash_amount DOUBLE,
    declaration_date DATE,
    pay_date DATE,
    record_date DATE,
    frequency INTEGER,                   -- 4=quarterly, 1=annual
    dividend_type VARCHAR(5),            -- CD=cash, SC=stock
    currency VARCHAR(5),
    PRIMARY KEY (ticker, ex_dividend_date)
);

-- Stock splits (event-based)
CREATE TABLE IF NOT EXISTS splits (
    ticker VARCHAR(10) NOT NULL,
    execution_date DATE NOT NULL,
    split_from INTEGER,
    split_to INTEGER,
    PRIMARY KEY (ticker, execution_date)
);

-- Ticker reference data (point-in-time, updateable)
CREATE TABLE IF NOT EXISTS ticker_details (
    ticker VARCHAR(10) PRIMARY KEY,
    name VARCHAR(255),
    market_cap DOUBLE,
    exchange VARCHAR(10),
    sic_code VARCHAR(10),
    sic_description VARCHAR(255),
    employees INTEGER,
    shares_outstanding DOUBLE,
    list_date DATE,
    currency VARCHAR(5),
    last_updated BIGINT                   -- Unix ms timestamp of last refresh
);

-- DuckDB is MVCC by default — no WAL pragma needed.
-- Concurrent reads during writes are handled natively.

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_daily_ticker ON daily_bars(ticker);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_bars(timestamp);
CREATE INDEX IF NOT EXISTS idx_hourly_ticker ON hourly_bars(ticker);
CREATE INDEX IF NOT EXISTS idx_hourly_date ON hourly_bars(timestamp);
CREATE INDEX IF NOT EXISTS idx_weekly_ticker ON weekly_bars(ticker);
CREATE INDEX IF NOT EXISTS idx_weekly_date ON weekly_bars(timestamp);
