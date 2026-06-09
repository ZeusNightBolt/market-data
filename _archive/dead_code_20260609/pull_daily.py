#!/usr/bin/env python3
"""
Two-phase daily bars pull: Phase 1 → NDJSON, Phase 2 → DuckDB bulk insert.
Drops existing daily_bars before re-inserting.
"""
import os, sys, json, time, logging
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from polygon_client import PolygonClient

BASE_DIR = Path.home() / "market-data"
RAW_DIR = BASE_DIR / "raw"
DB_PATH = BASE_DIR / "market_data.duckdb"
LOG_PATH = BASE_DIR / "pull_daily.log"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

HISTORY_START = "2021-06-01"
HISTORY_END = date.today().isoformat()
MAX_WORKERS = 8

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("dailypull")

client = PolygonClient()


def load_tickers():
    """Load ticker set from sector map CSV for filtering grouped daily results."""
    t = set()
    with open(SECTOR_MAP) as f:
        next(f)
        for line in f:
            ticker = line.split(",")[0].strip().upper()
            if ticker:
                t.add(ticker)
    return t


def generate_dates(start_str, end_str):
    """Generate all dates in range as ISO strings (trading and non-trading alike)."""
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)
    dates = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


# ── Phase 1: Pull daily bars → NDJSON ───────────────────────────────────

def pull_daily_for_date(target_date, ticker_set):
    """Pull daily bars for all US stocks on one date, filter to ticker_set, append to raw/daily.ndjson."""
    resp = client.grouped_daily(target_date, paginate=True)
    results = resp.get("results", [])
    if not results:
        return 0

    # Filter to only tickers we care about
    filtered = [r for r in results if r.get("T") in ticker_set]
    if not filtered:
        return 0

    out_path = RAW_DIR / "daily.ndjson"
    lines = [json.dumps(r) + "\n" for r in filtered]

    with open(out_path, "a") as f:
        f.writelines(lines)

    return len(filtered)


def pull_daily_parallel(dates, ticker_set):
    """Pull daily bars for all dates in parallel, filtering to ticker_set."""
    out_path = RAW_DIR / "daily.ndjson"
    if out_path.exists():
        out_path.unlink()

    log.info(f"Pulling daily bars: {len(dates):,} dates × {len(ticker_set):,} tickers ({MAX_WORKERS} workers)")
    t0 = time.time()
    total_rows = 0
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(pull_daily_for_date, d, ticker_set): d for d in dates}
        for f in as_completed(futures):
            d = futures[f]
            try:
                n = f.result()
                total_rows += n
                if n == 0:
                    errors += 1
            except Exception:
                errors += 1

            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed
                pct = done / len(dates) * 100
                log.info(f"  [{done:,}/{len(dates):,}] {pct:.0f}% — "
                         f"{total_rows:,} rows — {rate:.0f} dates/sec")

    elapsed = time.time() - t0
    size_mb = out_path.stat().st_size / 1024**2 if out_path.exists() else 0
    log.info(f"  Done: {total_rows:,} rows in {elapsed:.0f}s "
             f"({done/elapsed:.0f} dates/sec, {errors} empty/errors)")
    log.info(f"  File: {out_path} ({size_mb:.1f} MB)")
    return total_rows


# ── Phase 2: Bulk insert NDJSON → DuckDB ────────────────────────────────

def bulk_insert_daily():
    """Read daily.ndjson and recreate daily_bars using DuckDB native JSON reader."""
    path = RAW_DIR / "daily.ndjson"
    if not path.exists():
        log.info("No daily.ndjson — skipping")
        return

    import duckdb
    db = duckdb.connect(str(DB_PATH))

    log.info(f"Bulk inserting daily from {path.stat().st_size/1024**2:.0f}MB file...")
    t0 = time.time()

    # CREATE TABLE AS SELECT — avoids INSERT OR REPLACE ART index corruption
    db.execute("DROP TABLE IF EXISTS daily_bars")
    db.execute(f"""
        CREATE TABLE daily_bars AS
        SELECT
            T AS ticker,
            t::BIGINT AS timestamp,
            o::DOUBLE AS open,
            h::DOUBLE AS high,
            l::DOUBLE AS low,
            c::DOUBLE AS close,
            v::DOUBLE AS volume,
            vw::DOUBLE AS vwap,
            n::INTEGER AS transactions
        FROM read_ndjson_auto('{path}', ignore_errors=true)
    """)
    # Add PK after creation to avoid ART issues during CTAS
    db.execute("ALTER TABLE daily_bars ADD PRIMARY KEY (ticker, timestamp)")

    count = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    elapsed = time.time() - t0
    log.info(f"  Daily: {count:,} rows in {elapsed:.0f}s ({count/elapsed:.0f} rows/sec)")
    db.close()


# ── Main ────────────────────────────────────────────────────────────────

def main():
    import duckdb

    RAW_DIR.mkdir(exist_ok=True)

    # Phase 1
    log.info("\n" + "=" * 50)
    log.info("PHASE 1: Pulling daily bars → raw/daily.ndjson")
    log.info("=" * 50)

    ticker_set = load_tickers()
    dates = generate_dates(HISTORY_START, HISTORY_END)
    log.info(f"Loaded {len(ticker_set):,} tickers, {len(dates):,} dates")
    total = pull_daily_parallel(dates, ticker_set)

    log.info(f"\n✓ Phase 1 complete — {total:,} rows in raw/daily.ndjson")

    # Phase 2
    log.info("\n" + "=" * 50)
    log.info("PHASE 2: Bulk inserting into DuckDB")
    log.info("=" * 50)

    bulk_insert_daily()

    # Verify
    db = duckdb.connect(str(DB_PATH))
    c = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    size = DB_PATH.stat().st_size / 1024**2
    log.info(f"\n  daily_bars: {c:,} rows | DB size: {size:.1f} MB")
    db.close()

    log.info("\n✅ Daily bars pull complete")
    print("✅ Complete")


if __name__ == "__main__":
    main()
