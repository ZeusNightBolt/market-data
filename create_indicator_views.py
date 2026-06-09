#!/usr/bin/env python3
"""
Create DuckDB SQL views for on-demand technical indicators.
These views compute SMA instantly from the raw bar tables — 
no pre-computation, no storage overhead, always current.

Views created:
  v_indicators_daily  — SMA 20/50/200 + volume averages + volume spike flag
  v_indicators_1h     — SMA 20/50/200 on 1h bars
  v_indicators_4h     — SMA 20/50/200 on resampled 4h bars

Usage:
  python3 create_indicator_views.py          # create/replace all views
  python3 create_indicator_views.py --drop   # remove all views
"""

import sys
from pathlib import Path
import duckdb

DB_PATH = Path.home() / "market-data" / "market_data.duckdb"

VIEWS_SQL = """
-- ═══════════════════════════════════════════════════════════════════════
-- DAILY: SMA 20/50/200 + volume spike detection
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_indicators_daily AS
WITH base AS (
    SELECT
        ticker,
        timestamp,
        open,
        high,
        low,
        close,
        volume,
        AVG(close) OVER (
            PARTITION BY ticker ORDER BY timestamp
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS sma_20,
        AVG(close) OVER (
            PARTITION BY ticker ORDER BY timestamp
            ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
        ) AS sma_50,
        AVG(close) OVER (
            PARTITION BY ticker ORDER BY timestamp
            ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
        ) AS sma_200,
        AVG(volume) OVER (
            PARTITION BY ticker ORDER BY timestamp
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS avg_vol_20
    FROM daily_bars
),
vol_spike AS (
    SELECT
        *,
        -- 20-day avg EXCLUDING current bar (shifted)
        AVG(volume) OVER (
            PARTITION BY ticker ORDER BY timestamp
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_vol_20_prev
    FROM base
)
SELECT
    ticker,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    sma_20,
    sma_50,
    sma_200,
    avg_vol_20,
    CASE
        WHEN avg_vol_20_prev > 0 AND volume > avg_vol_20_prev * 3.0
        THEN TRUE ELSE FALSE
    END AS volume_spike
FROM vol_spike;

-- ═══════════════════════════════════════════════════════════════════════
-- 1H: SMA 20/50/200 on hourly bars
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_indicators_1h AS
SELECT
    ticker,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS sma_20,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
    ) AS sma_50,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
    ) AS sma_200
FROM hourly_bars;

-- ═══════════════════════════════════════════════════════════════════════
-- 4H: Resampled from hourly → SMA 20/50/200
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_indicators_4h AS
WITH bars_4h AS (
    SELECT
        ticker,
        (timestamp / 14400000)::BIGINT * 14400000 AS bucket_ts,
        FIRST_VALUE(open) OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
            ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS open,
        MAX(high) OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
        ) AS high,
        MIN(low) OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
        ) AS low,
        LAST_VALUE(close) OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
            ORDER BY timestamp
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS close,
        SUM(volume) OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
        ) AS volume,
        ROW_NUMBER() OVER (
            PARTITION BY ticker, (timestamp / 14400000)::BIGINT
            ORDER BY timestamp
        ) AS rn
    FROM hourly_bars
),
deduped AS (
    SELECT ticker, bucket_ts AS timestamp, open, high, low, close, volume
    FROM bars_4h
    WHERE rn = 1
)
SELECT
    ticker,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS sma_20,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
    ) AS sma_50,
    AVG(close) OVER (
        PARTITION BY ticker ORDER BY timestamp
        ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
    ) AS sma_200
FROM deduped;

-- ═══════════════════════════════════════════════════════════════════════
-- PIVOT POINTS: Classic floor pivots from prior day HLC
-- PP = (H+L+C)/3, R1 = 2*PP-L, S1 = 2*PP-H, R2 = PP+(H-L), S2 = PP-(H-L)
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_pivots_daily AS
WITH prior AS (
    SELECT ticker, timestamp,
           LAG(high, 1)  OVER (PARTITION BY ticker ORDER BY timestamp) AS prev_high,
           LAG(low, 1)   OVER (PARTITION BY ticker ORDER BY timestamp) AS prev_low,
           LAG(close, 1) OVER (PARTITION BY ticker ORDER BY timestamp) AS prev_close,
           open, high, low, close, volume
    FROM daily_bars
)
SELECT ticker, timestamp, open, high, low, close, volume,
       (prev_high + prev_low + prev_close) / 3.0          AS pivot_pp,
       (2.0 * (prev_high + prev_low + prev_close) / 3.0) - prev_low  AS pivot_r1,
       (2.0 * (prev_high + prev_low + prev_close) / 3.0) - prev_high AS pivot_s1,
       ((prev_high + prev_low + prev_close) / 3.0) + (prev_high - prev_low) AS pivot_r2,
       ((prev_high + prev_low + prev_close) / 3.0) - (prev_high - prev_low) AS pivot_s2
FROM prior
WHERE prev_high IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- BOLLINGER BANDS (20, 2.0): SMA ± 2σ — reuses existing SMA views
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_bollinger_daily AS
WITH rolling AS (
    SELECT ticker, timestamp, open, high, low, close, volume,
           AVG(close) OVER (
               PARTITION BY ticker ORDER BY timestamp
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS sma_20,
           STDDEV_SAMP(close) OVER (
               PARTITION BY ticker ORDER BY timestamp
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS std_20
    FROM daily_bars
)
SELECT ticker, timestamp, open, high, low, close, volume,
       sma_20,
       sma_20 + 2.0 * std_20 AS bb_upper,
       sma_20 - 2.0 * std_20 AS bb_lower,
       (4.0 * std_20) / NULLIF(sma_20, 0) AS bb_width
FROM rolling
WHERE std_20 IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- VWAP BANDS: Daily VWAP-based support/resistance levels
-- VWAP ± 1σ, ±2σ of close distribution (20-day rolling)
-- ═══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW v_vwap_bands AS
WITH vwap_data AS (
    SELECT ticker, timestamp, open, high, low, close, volume, vwap,
           AVG(close) OVER (
               PARTITION BY ticker ORDER BY timestamp
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS sma_20,
           STDDEV_SAMP(close) OVER (
               PARTITION BY ticker ORDER BY timestamp
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS std_20
    FROM daily_bars
    WHERE vwap IS NOT NULL AND vwap > 0
)
SELECT ticker, timestamp, open, high, low, close, volume,
       vwap,
       ABS(close - vwap) / NULLIF(vwap, 0) * 100.0 AS vwp_pct,
       vwap + 1.0 * std_20 AS vwap_r1,
       vwap - 1.0 * std_20 AS vwap_s1,
       vwap + 2.0 * std_20 AS vwap_r2,
       vwap - 2.0 * std_20 AS vwap_s2
FROM vwap_data
WHERE std_20 IS NOT NULL;
"""

DROP_SQL = """
DROP VIEW IF EXISTS v_indicators_daily;
DROP VIEW IF EXISTS v_indicators_1h;
DROP VIEW IF EXISTS v_indicators_4h;
DROP VIEW IF EXISTS v_pivots_daily;
DROP VIEW IF EXISTS v_bollinger_daily;
DROP VIEW IF EXISTS v_vwap_bands;
"""


def create_views():
    db = duckdb.connect(str(DB_PATH))
    db.execute(VIEWS_SQL)
    db.close()
    print("✅ Views created: v_indicators_daily, v_indicators_1h, v_indicators_4h, v_pivots_daily, v_bollinger_daily, v_vwap_bands")


def drop_views():
    db = duckdb.connect(str(DB_PATH))
    db.execute(DROP_SQL)
    db.close()
    print("✅ Views dropped")


def verify():
    db = duckdb.connect(str(DB_PATH))
    
    # View groups with different verification queries
    indicator_views = ["v_indicators_daily", "v_indicators_1h", "v_indicators_4h"]
    summary_views = ["v_pivots_daily", "v_bollinger_daily", "v_vwap_bands"]
    
    for view in indicator_views + summary_views:
        count = db.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
        tickers = db.execute(f"SELECT COUNT(DISTINCT ticker) FROM {view}").fetchone()[0]
        print(f"\n── {view} ──")
        print(f"  {count:,} rows  |  {tickers:,} tickers")
        
        if view in indicator_views:
            latest = db.execute(f"""
                SELECT ticker, close, sma_20, sma_50, sma_200 
                FROM {view} WHERE ticker = 'AAPL' 
                ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            if latest:
                tkr, c, s20, s50, s200 = latest
                print(f"  AAPL latest: C=${c:.2f}  SMA20=${s20:.2f}  SMA50=${s50:.2f}  SMA200=${s200:.2f}")
    
    # Also test volume spike detection
    spikes = db.execute("""
        SELECT COUNT(*) FROM v_indicators_daily 
        WHERE ticker = 'AAPL' AND volume_spike = TRUE
    """).fetchone()[0]
    print(f"\n  AAPL volume spikes (view): {spikes}")
    
    # Test 4h count is reasonable
    bars_per_tkr = db.execute("""
        SELECT AVG(cnt) FROM (
            SELECT ticker, COUNT(*) as cnt FROM v_indicators_4h GROUP BY ticker
        )
    """).fetchone()[0]
    print(f"  4h avg bars per ticker: {bars_per_tkr:.0f}")
    
    db.close()


if __name__ == "__main__":
    if "--drop" in sys.argv:
        drop_views()
    else:
        drop_views()  # clean slate — if create fails, viewers have zero views
        try:
            create_views()
        except Exception:
            print("❌ View creation failed! Views are in dropped state. "
                  "Re-run once the underlying data issue is resolved.",
                  file=sys.stderr)
            raise
        verify()
