#!/usr/bin/env python3
"""
Build higher-timeframe bars from hourly data — zero API calls.
===============================================================

Derives daily and weekly OHLCV bars by aggregating hourly_bars in DuckDB.
Eliminates daily/weekly Polygon API pulls for the steady-state pipeline.
API pulls are kept as fallback only when hourly data doesn't cover a ticker.

Architecture:
  hourly_bars (API - pull_hourly.py)
    └── v_daily_from_hourly → upserted into daily_bars
          └── v_weekly_bars → upserted into weekly_bars

Usage:
  python3 build_higher_timeframes.py              # aggregate + upsert
  python3 build_higher_timeframes.py --dry-run    # show what would change
  python3 build_higher_timeframes.py --api-fallback  # pull from API for gaps
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import duckdb

BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

# Thresholds
MIN_HOURLY_BARS_FOR_DAILY = 6   # at least 6 hours of trading for a valid daily bar
MIN_DAILY_BARS_FOR_WEEKLY = 3   # at least 3 trading days for a valid weekly bar


# ── Views ──────────────────────────────────────────────────────────────────

DAILY_FROM_HOURLY_SQL = """
CREATE OR REPLACE VIEW v_daily_from_hourly AS
WITH aggregated AS (
    SELECT
        ticker,
        -- Integer division truncates to UTC day boundary (midnight)
        (timestamp // 86400000::BIGINT) * 86400000::BIGINT AS day_ts,
        FIRST(open ORDER BY timestamp) AS open,
        MAX(high) AS high,
        MIN(low) AS low,
        LAST(close ORDER BY timestamp) AS close,
        SUM(volume) AS volume,
        CASE WHEN SUM(volume) > 0
             THEN SUM(volume * COALESCE(vwap, close)) / SUM(volume)
             ELSE LAST(close ORDER BY timestamp)
        END AS vwap,
        COUNT(*) AS bar_count,
        MIN(timestamp) AS first_ts,
        MAX(timestamp) AS last_ts
    FROM hourly_bars
    GROUP BY ticker, day_ts
)
SELECT ticker, day_ts AS timestamp, open, high, low, close, volume, vwap,
       bar_count, first_ts, last_ts
FROM aggregated
WHERE bar_count >= {min_bars}
  -- The bar_count gate above already prevents incomplete intraday daily bars
  -- (pre-market bars at the 09:30 ET cron run won't reach 6+ bars).  The
  -- prior ``current_date`` filter was overly conservative — it permanently
  -- blocked today's daily bar from forming, which starved the downstream
  -- technical-indicator and RSI-dashboard pipelines when they run after
  -- market close (20:30 ET).  Removing it lets the bar_count check carry
  -- the incompleteness guard alone.
"""


WEEKLY_FROM_DAILY_SQL = """
CREATE OR REPLACE VIEW v_weekly_bars AS
WITH daily_with_dow AS (
    SELECT *,
           EXTRACT(DOW FROM epoch_ms(timestamp))::INT AS dow
    FROM daily_bars
),
week_aligned AS (
    SELECT ticker,
           -- Align to Sunday 00:00 UTC (matches Polygon's weekly bar timestamp convention)
           timestamp - (dow - 1) * 86400000::BIGINT + 6::BIGINT * 86400000::BIGINT AS week_ts,
           open, high, low, close, volume, vwap,
           timestamp AS orig_ts
    FROM daily_with_dow
    WHERE dow BETWEEN 1 AND 5
),
aggregated AS (
    SELECT ticker, week_ts,
           FIRST(open ORDER BY orig_ts) AS open,
           MAX(high) AS high, MIN(low) AS low,
           LAST(close ORDER BY orig_ts) AS close,
           SUM(volume) AS volume,
           CASE WHEN SUM(volume) > 0
                THEN SUM(volume * COALESCE(vwap, close)) / SUM(volume)
                ELSE LAST(close ORDER BY orig_ts)
           END AS vwap,
           COUNT(*) AS bar_count,
           MIN(orig_ts) AS first_ts, MAX(orig_ts) AS last_ts
    FROM week_aligned
    GROUP BY ticker, week_ts
)
SELECT ticker, week_ts AS timestamp, open, high, low, close, volume, vwap,
       bar_count, first_ts, last_ts
FROM aggregated
WHERE bar_count >= {min_bars}
"""


# ── Materialization ────────────────────────────────────────────────────────

def create_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create or replace the aggregation views."""
    con.execute(DAILY_FROM_HOURLY_SQL.format(min_bars=MIN_HOURLY_BARS_FOR_DAILY))
    con.execute(WEEKLY_FROM_DAILY_SQL.format(min_bars=MIN_DAILY_BARS_FOR_WEEKLY))
    print("✅ Views created: v_daily_from_hourly, v_weekly_bars")


def ensure_indexes(con: duckdb.DuckDBPyConnection) -> None:
    """Ensure indexes exist for fast upserts."""
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_hourly_ticker_ts
        ON hourly_bars(ticker, timestamp)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_ticker_ts
        ON daily_bars(ticker, timestamp)
    """)


def materialize_daily(con: duckdb.DuckDBPyConnection, dry_run: bool = False) -> int:
    """Upsert provisional daily bars from v_daily_from_hourly into daily_bars.

    Matches by local market calendar date rather than exact timestamp because
    Polygon's grouped endpoint, per-ticker daily endpoint, and hourly-derived
    bars use different intraday timestamp anchors for the same US trading
    session. Hourly-derived rows have NULL transactions and are provisional;
    daily_append.py replaces them with official Polygon daily rows when those
    arrive.
    """
    # Count new bars
    new_count = con.execute("""
        SELECT COUNT(*)
        FROM v_daily_from_hourly v
        WHERE NOT EXISTS (
            SELECT 1 FROM daily_bars d
            WHERE d.ticker = v.ticker
              AND epoch_ms(d.timestamp::BIGINT)::DATE = epoch_ms(v.timestamp::BIGINT)::DATE
        )
    """).fetchone()[0]

    if new_count == 0:
        print("  daily_from_hourly: 0 new bars (all dates already in daily_bars)")
        return 0

    if dry_run:
        # Show sample
        sample = con.execute("""
            SELECT ticker, epoch_ms(v.timestamp::BIGINT)::DATE AS dt, bar_count
            FROM v_daily_from_hourly v
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_bars d
                WHERE d.ticker = v.ticker
                  AND epoch_ms(d.timestamp::BIGINT)::DATE = epoch_ms(v.timestamp::BIGINT)::DATE
            )
            ORDER BY v.timestamp DESC
            LIMIT 5
        """).fetchall()
        print(f"  DRY RUN: would upsert {new_count:,} daily bars")
        for tkr, dt, bc in sample:
            print(f"    {tkr:6s}  {dt}  {bc} hourly bars")
        return new_count

    # Insert new bars
    t0 = time.time()
    con.execute("""
        INSERT INTO daily_bars (ticker, timestamp, open, high, low, close, volume, vwap)
        SELECT v.ticker, v.timestamp, v.open, v.high, v.low, v.close, v.volume, v.vwap
        FROM v_daily_from_hourly v
        WHERE NOT EXISTS (
            SELECT 1 FROM daily_bars d
            WHERE d.ticker = v.ticker
              AND epoch_ms(d.timestamp::BIGINT)::DATE = epoch_ms(v.timestamp::BIGINT)::DATE
        )
    """)
    elapsed = time.time() - t0

    total = con.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    tickers = con.execute("SELECT COUNT(DISTINCT ticker) FROM daily_bars").fetchone()[0]
    print(f"  daily_from_hourly: +{new_count:,} bars → {total:,} total, {tickers:,} tickers in {elapsed:.1f}s")
    return new_count


def materialize_weekly(con: duckdb.DuckDBPyConnection, dry_run: bool = False) -> int:
    """Upsert weekly bars from v_weekly_bars into weekly_bars.

    Matches by (ticker, week_start_date) rather than exact timestamp,
    because the API's weekly bar timestamps use Sunday-end convention
    while our view uses Monday-start convention.
    """
    new_count = con.execute("""
        SELECT COUNT(*)
        FROM v_weekly_bars v
        WHERE NOT EXISTS (
            SELECT 1 FROM weekly_bars w
            WHERE w.ticker = v.ticker
              AND epoch_ms(w.timestamp)::DATE = epoch_ms(v.timestamp)::DATE
        )
    """).fetchone()[0]

    if new_count == 0:
        print("  weekly_from_daily: 0 new bars (all weeks already in weekly_bars)")
        return 0

    if dry_run:
        sample = con.execute("""
            SELECT ticker, epoch_ms(v.timestamp)::DATE AS week_of, bar_count
            FROM v_weekly_bars v
            WHERE NOT EXISTS (
                SELECT 1 FROM weekly_bars w
                WHERE w.ticker = v.ticker
                  AND epoch_ms(w.timestamp)::DATE = epoch_ms(v.timestamp)::DATE
            )
            ORDER BY v.timestamp DESC
            LIMIT 5
        """).fetchall()
        print(f"  DRY RUN: would upsert {new_count:,} weekly bars")
        for tkr, wo, bc in sample:
            print(f"    {tkr:6s}  week of {wo}  {bc} daily bars")
        return new_count

    t0 = time.time()
    con.execute("""
        INSERT INTO weekly_bars (ticker, timestamp, open, high, low, close, volume, vwap)
        SELECT v.ticker, v.timestamp, v.open, v.high, v.low, v.close, v.volume, v.vwap
        FROM v_weekly_bars v
        WHERE NOT EXISTS (
            SELECT 1 FROM weekly_bars w
            WHERE w.ticker = v.ticker
              AND epoch_ms(w.timestamp)::DATE = epoch_ms(v.timestamp)::DATE
        )
    """)
    elapsed = time.time() - t0

    total = con.execute("SELECT COUNT(*) FROM weekly_bars").fetchone()[0]
    tickers = con.execute("SELECT COUNT(DISTINCT ticker) FROM weekly_bars").fetchone()[0]
    last_wk = con.execute(
        "SELECT epoch_ms(MAX(timestamp))::DATE FROM weekly_bars"
    ).fetchone()[0]
    print(f"  weekly_from_daily: +{new_count:,} bars → {total:,} total, "
          f"{tickers:,} tickers, last={last_wk} in {elapsed:.1f}s")
    return new_count


def api_fallback_daily() -> int:
    """Pull daily bars from Polygon API after the parent DuckDB handle is closed.

    `daily_append.py` is intentionally a separate NDJSON-first process.  It opens
    its own DuckDB connection only for short bulk merges.  Calling it while this
    script still holds a DuckDB connection causes a self-inflicted lock conflict
    even though no other cron is overlapping the refresh.
    """
    import subprocess
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "daily_append.py")],
        capture_output=True, text=True, timeout=600
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if "Conflicting lock" in err or "set lock" in err:
            print(
                "⚠️  API fallback daily_append failed with DuckDB lock contention. "
                "This should only happen if another process holds the DB lock; "
                "build_higher_timeframes now closes its own connection before "
                "spawning daily_append. Full stderr follows:\n"
                f"{err[-1000:]}"
            )
        else:
            print(f"⚠️  API fallback daily_append failed:\n{err[-1000:]}")
        return 0
    return 1


def api_fallback_weekly(con: duckdb.DuckDBPyConnection) -> int:
    """Pull weekly bars from Polygon API for tickers missing from weekly_bars.

    Uses fast_pull.py but only for tickers with zero weekly coverage.
    """
    tickers_missing = con.execute("""
        SELECT DISTINCT d.ticker
        FROM daily_bars d
        WHERE d.ticker NOT IN (SELECT DISTINCT ticker FROM weekly_bars)
        LIMIT 200
    """).fetchall()

    if not tickers_missing:
        print("  No tickers missing weekly coverage — skipping API fallback")
        return 0

    ticker_list = [r[0] for r in tickers_missing]
    print(f"  API fallback: pulling weekly for {len(ticker_list)} tickers without coverage")

    # Targeted API pull — only for specific tickers, recent date range
    import json, threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    sys.path.insert(0, str(BASE_DIR))
    from polygon_client import PolygonClient
    client = PolygonClient(timeout=30, retries=3)

    out_path = BASE_DIR / "raw" / "weekly_fallback.ndjson"
    if out_path.exists():
        out_path.unlink()

    stats = {"done": 0, "rows": 0}
    lock = threading.Lock()
    write_lock = threading.Lock()

    def pull_one(ticker):
        try:
            resp = client._get(
                f"/v2/aggs/ticker/{ticker}/range/1/week/2026-01-01/{date.today().isoformat()}",
                {"limit": 50000, "sort": "asc"}
            )
            results = resp.get("results", [])
            if not results:
                with lock: stats["done"] += 1
                return

            lines = []
            for r in results:
                lines.append(json.dumps({
                    "_ticker": ticker, "t": r.get("t"),
                    "o": r.get("o"), "h": r.get("h"), "l": r.get("l"),
                    "c": r.get("c"), "v": r.get("v"),
                    "vw": r.get("vw"), "n": r.get("n")
                }) + "\n")

            with write_lock:
                with open(out_path, "a") as f:
                    f.writelines(lines)
            with lock:
                stats["done"] += 1
                stats["rows"] += len(results)
        except Exception:
            with lock:
                stats["done"] += 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(pull_one, t): t for t in ticker_list}
        for f in as_completed(futures):
            try:
                f.result(timeout=60)
            except Exception:
                pass

    if stats["rows"] > 0:
        before = con.execute("SELECT COUNT(*) FROM weekly_bars").fetchone()[0]
        con.execute(f"""
            INSERT OR IGNORE INTO weekly_bars
            SELECT j._ticker, j.t, j.o, j.h, j.l, j.c, j.v, j.vw, j.n
            FROM read_json('{out_path}', format='newline_delimited',
                           columns={{_ticker: 'VARCHAR', t: 'BIGINT', o: 'DOUBLE',
                                     h: 'DOUBLE', l: 'DOUBLE', c: 'DOUBLE',
                                     v: 'DOUBLE', vw: 'DOUBLE', n: 'INTEGER'}}) j
        """)
        after = con.execute("SELECT COUNT(*) FROM weekly_bars").fetchone()[0]
        print(f"  API fallback weekly: +{after - before:,} bars")
        out_path.unlink()
    else:
        print("  API fallback weekly: 0 bars pulled")
    return stats["rows"]


# ── Gap detection ──────────────────────────────────────────────────────────

def show_gaps(con: duckdb.DuckDBPyConnection) -> None:
    """Show tickers where API data exists but hourly-derived data doesn't cover."""
    # Tickers with daily API data but no hourly-derived daily bars
    missing_daily = con.execute("""
        SELECT COUNT(DISTINCT d.ticker)
        FROM daily_bars d
        WHERE d.ticker NOT IN (
            SELECT DISTINCT ticker FROM v_daily_from_hourly
            WHERE timestamp > (SELECT MAX(timestamp) FROM v_daily_from_hourly) - 30::BIGINT * 86400000::BIGINT
        )
    """).fetchone()[0]

    # Tickers with weekly API data but no daily-derived weekly bars
    missing_weekly = con.execute("""
        SELECT COUNT(DISTINCT w.ticker)
        FROM weekly_bars w
        WHERE w.ticker NOT IN (
            SELECT DISTINCT ticker FROM v_weekly_bars
        )
    """).fetchone()[0]

    print(f"  Gap report: {missing_daily} tickers with API daily but no hourly-derived daily")
    print(f"              {missing_weekly} tickers with API weekly but no daily-derived weekly")


# ── Main ───────────────────────────────────────────────────────────────────

def configure_connection(con: duckdb.DuckDBPyConnection) -> None:
    """Apply the warehouse-safe DuckDB settings used by this batch job."""
    con.execute("SET memory_limit = '8GB'")
    con.execute("SET threads = 2")
    con.execute("SET preserve_insertion_order = false")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build higher-timeframe bars from hourly data — zero API calls by default"
    )
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be upserted, don't write")
    ap.add_argument("--api-fallback", action="store_true",
                    help="Pull from Polygon API for tickers with gaps in hourly data")
    ap.add_argument("--skip-daily", action="store_true")
    ap.add_argument("--skip-weekly", action="store_true")
    args = ap.parse_args()

    con: duckdb.DuckDBPyConnection | None = duckdb.connect(args.db)
    configure_connection(con)

    try:
        ensure_indexes(con)
        create_views(con)

        if not args.skip_daily:
            print("\n== Daily bars (1h → daily aggregation) ==")
            materialize_daily(con, dry_run=args.dry_run)

        if not args.skip_weekly:
            print("\n== Weekly bars (daily → weekly aggregation) ==")
            materialize_weekly(con, dry_run=args.dry_run)

        if args.api_fallback:
            print("\n== API fallback (tickers without hourly coverage) ==")
            # IMPORTANT: close this script's DuckDB connection before spawning
            # daily_append.py. DuckDB allows only one writer, and even this
            # parent process's open connection can block the child merge with
            # "Conflicting lock is held". Re-open afterward for the weekly
            # fallback, which runs in-process.
            con.close()
            con = None
            api_fallback_daily()
            con = duckdb.connect(args.db)
            configure_connection(con)
            api_fallback_weekly(con)

        if args.dry_run:
            print("\n== Gap analysis ==")
            show_gaps(con)

        if not args.dry_run:
            print("\n✅ Higher timeframe build complete")
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    main()
