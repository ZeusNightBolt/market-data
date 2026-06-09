#!/usr/bin/env python3
"""Safe pipeline smoke test for the market-data schema.

Default mode is intentionally NO-API and NON-DESTRUCTIVE: it creates a temporary
DuckDB database, loads schema.sql, inserts a deterministic daily_bars fixture,
verifies the fast daily indicator updater, then removes the temp DB.

For a live Polygon grouped-endpoint probe, pass --live-api.  Even in live mode
this script still uses only a temp DB and never unlinks ~/market-data/market_data.duckdb.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import duckdb

BASE_DIR = Path.home() / "market-data"
PROD_DB_PATH = BASE_DIR / "market_data.duckdb"
SCHEMA_PATH = BASE_DIR / "schema.sql"
ENV_FILE = Path.home() / ".hermes" / ".env"


def default_grouped_day() -> str:
    d = date.today() - timedelta(days=1)
    if d.weekday() >= 5:
        d = d - timedelta(days=d.weekday() - 4)
    return d.isoformat()


def load_polygon_api_key() -> str | None:
    if os.environ.get("POLYGON_API_KEY"):
        return os.environ["POLYGON_API_KEY"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("POLYGON_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_test_tickers(limit: int) -> list[str]:
    sector_map = Path.home() / "earnings-reports" / "sector_map_vti.csv"
    preferred = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "JPM", "LLY", "V"]
    if sector_map.exists():
        with sector_map.open() as f:
            next(f, None)
            universe = [line.split(",")[0].strip().upper() for line in f if "," in line]
        universe = [t for t in universe if t]
        universe_set = set(universe)
        liquid = [t for t in preferred if t in universe_set]
        if len(liquid) >= limit:
            return liquid[:limit]
        # Keep the fixture deterministic but avoid OTC/foreign-style symbols for
        # live grouped-endpoint probes by preferring common US ticker shapes.
        simple = [t for t in universe if t.isalpha() and 1 <= len(t) <= 5 and t not in set(liquid)]
        if liquid or simple:
            return (liquid + simple)[:limit]
    return preferred[:limit]


def init_temp_db() -> tuple[tempfile.TemporaryDirectory[str], Path, duckdb.DuckDBPyConnection]:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Missing schema file: {SCHEMA_PATH}")
    tmpdir = tempfile.TemporaryDirectory(prefix="market-data-pipeline-smoke-")
    db_path = Path(tmpdir.name) / "market_data_smoke.duckdb"
    db = duckdb.connect(str(db_path))
    db.execute(SCHEMA_PATH.read_text())
    print(f"✓ Temp DB initialized: {db_path}")
    print(f"✓ Production DB untouched: {PROD_DB_PATH}")
    return tmpdir, db_path, db


def insert_fixture_rows(db: duckdb.DuckDBPyConnection, tickers: list[str]) -> int:
    rows = []
    base_ts = 1_704_067_200_000  # 2024-01-01 UTC
    day_ms = 86_400_000
    # 60 bars/ticker exercise refresh_latest_daily_indicators.py, which skips
    # tickers with <50 bars to avoid unstable RSI/ATR seeds.
    for i, ticker in enumerate(tickers):
        for j in range(60):
            close = 100.5 + i + j * 0.1
            rows.append((
                ticker,
                base_ts + j * day_ms,
                close - 0.5,
                close + 0.5,
                close - 1.0,
                close,
                1_000_000 + i * 100 + j,
                close - 0.1,
                100 + j,
            ))
    db.executemany(
        "INSERT OR IGNORE INTO daily_bars "
        "(ticker, timestamp, open, high, low, close, volume, vwap, transactions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def pull_live_grouped_rows(day: str, tickers: set[str], api_key: str) -> list[tuple]:
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{day}?limit=50000&apiKey={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "Hermes-Smoke-Test/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    rows = []
    for bar in data.get("results", []):
        ticker = bar.get("T", "")
        if ticker in tickers:
            rows.append((ticker, bar["t"], bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"), bar.get("v"), bar.get("vw"), bar.get("n")))
    print(f"  {day}: {len(rows)} bars from {len(data.get('results', []))} grouped results")
    return rows


def create_minimal_indicator_view(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""
        CREATE OR REPLACE VIEW v_indicators_daily AS
        SELECT ticker,
               timestamp,
               AVG(close) OVER (PARTITION BY ticker ORDER BY timestamp ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma_20,
               AVG(close) OVER (PARTITION BY ticker ORDER BY timestamp ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma_50,
               AVG(close) OVER (PARTITION BY ticker ORDER BY timestamp ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma_200,
               volume > AVG(volume) OVER (PARTITION BY ticker ORDER BY timestamp ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) * 2 AS volume_spike
        FROM daily_bars
    """)


def verify_fast_indicator_updater(db: duckdb.DuckDBPyConnection, tickers: list[str]) -> None:
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    from refresh_latest_daily_indicators import compute_latest, upsert

    create_minimal_indicator_view(db)
    df = compute_latest(db, tickers=tickers, lookback_rows=60)
    assert len(df) == len(tickers), f"expected {len(tickers)} latest indicator rows, got {len(df)}"
    assert df["rsi_14"].notna().all(), "rsi_14 should be populated for fixture rows"
    assert df["atr_14"].notna().all(), "atr_14 should be populated for fixture rows"
    upsert(db, df)
    inserted = db.execute("SELECT COUNT(*) FROM technical_indicators WHERE timeframe='daily'").fetchone()[0]
    assert inserted == len(tickers), f"expected {len(tickers)} technical indicator rows, got {inserted}"
    print(f"✓ Fast indicator updater computed/upserted {inserted} daily rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe market-data pipeline smoke test")
    parser.add_argument("--live-api", action="store_true", help="Call Polygon grouped endpoint and load live rows into the temp DB")
    parser.add_argument("--day", default=default_grouped_day(), help="Grouped endpoint date for --live-api")
    parser.add_argument("--tickers", type=int, default=5, help="Number of VTI tickers to test")
    args = parser.parse_args()

    tickers = load_test_tickers(args.tickers)
    print(f"✓ Test universe: {len(tickers)} tickers")

    tmpdir, db_path, db = init_temp_db()
    try:
        if args.live_api:
            api_key = load_polygon_api_key()
            if not api_key:
                raise RuntimeError("POLYGON_API_KEY not found in environment or ~/.hermes/.env")
            rows = pull_live_grouped_rows(args.day, set(tickers), api_key)
            if rows:
                db.executemany(
                    "INSERT OR IGNORE INTO daily_bars "
                    "(ticker, timestamp, open, high, low, close, volume, vwap, transactions) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            # Live grouped endpoint returns one bar per ticker; the indicator
            # updater needs a 50+ bar lookback, so keep the API probe and add
            # deterministic fixture history for the updater smoke test.
            inserted = insert_fixture_rows(db, tickers)
            print(f"✓ Inserted {inserted} fixture daily_bars rows for updater lookback")
        else:
            inserted = insert_fixture_rows(db, tickers)
            print(f"✓ Inserted {inserted} fixture daily_bars rows (no API)")

        verify_fast_indicator_updater(db, tickers)

        count = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        distinct = db.execute("SELECT COUNT(DISTINCT ticker) FROM daily_bars").fetchone()[0]
        sample = db.execute(
            "SELECT ticker, COUNT(*) AS bars, MIN(close) AS min_c, MAX(close) AS max_c "
            "FROM daily_bars GROUP BY ticker ORDER BY ticker LIMIT 5"
        ).fetchall()
        assert count > 0, "daily_bars count should be positive"
        assert distinct > 0, "daily_bars ticker count should be positive"

        print("\n✓ VALIDATION PASSED")
        print(f"  Temp DB: {db_path}")
        print(f"  Total: {count} bars across {distinct} tickers")
        print("  Sample:")
        for row in sample:
            print(f"    {row[0]}: {row[1]} bars, range ${row[2]:.2f}-${row[3]:.2f}")
    finally:
        db.close()
        tmpdir.cleanup()
        print("\n✓ Temp DB removed; production DB was not modified")


if __name__ == "__main__":
    main()
