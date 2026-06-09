#!/usr/bin/env python3
"""
Market Data Warehouse — Daily Append v3 (NDJSON-first)
=======================================================
Pulls daily bars for a target date. Tries grouped endpoint first (1 call).
Falls back to per-ticker parallel pulls if grouped returns empty.

HARD RULE: Pull to NDJSON first. Bulk-merge into DuckDB at the end.
Zero DB contention during pull phase — runs alongside hourly/ticker_details.

Usage:
  python3 daily_append.py              # append yesterday
  python3 daily_append.py --date 2026-05-16  # specific date
  python3 daily_append.py --dry-run    # show what would happen
  python3 daily_append.py --insert-only  # just merge existing NDJSON
"""

import sys, time, json, threading, queue
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from polygon_client import PolygonClient

# ── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
RAW_DIR = BASE_DIR / "raw"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

MAX_WORKERS_FALLBACK = 4   # per-ticker fallback: 4 concurrent
API_TIMEOUT = 30
CALL_GAP = 1.5             # seconds between per-ticker call starts


def load_vti_tickers() -> set[str]:
    tickers = set()
    with open(SECTOR_MAP) as f:
        next(f)
        for line in f:
            t = line.split(",")[0].strip().upper()
            if t:
                tickers.add(t)
    return tickers


# ── Primary: grouped daily (fast path) ────────────────────────────────────

def pull_grouped_to_ndjson(client: PolygonClient, target_date: str,
                           vti_set: set[str], ndjson_path: Path) -> int:
    """
    Pull grouped daily bars. Filter to VTI tickers. Write NDJSON.
    Returns number of VTI bars written.
    """
    resp = client.grouped_daily(target_date, paginate=True)
    results = resp.get("results", [])
    # Aggregate endpoints can return valid payloads without the standard
    # status='OK' field. Use PolygonClient's endpoint-aware success check,
    # not _ok(), or the grouped fast path can be falsely treated as empty.
    if not results or not client._success(resp, "/v2/aggs/grouped"):
        return 0

    written = 0
    with open(ndjson_path, "a") as f:
        for bar in results:
            t = bar.get("T", "")
            if t not in vti_set:
                continue
            f.write(json.dumps({
                "_ticker": t, "t": bar.get("t"),
                "o": bar.get("o"), "h": bar.get("h"), "l": bar.get("l"),
                "c": bar.get("c"), "v": bar.get("v"),
                "vw": bar.get("vw"), "n": bar.get("n")
            }) + "\n")
            written += 1

    return written


# ── Fallback: per-ticker daily → NDJSON ───────────────────────────────────

def pull_ticker_daily(client: PolygonClient, ticker: str,
                      target_date: str, ndjson_path: Path,
                      write_lock: threading.Lock) -> bool:
    """
    Pull one day of daily bars for a ticker. Write NDJSON line if successful.
    Returns True if data was found and written.
    """
    resp = client.custom_bars(ticker, target_date, target_date,
                               timespan="day", limit=10, sort="asc")
    results = resp.get("results", [])
    if not results:
        return False

    bar = results[-1]
    if bar.get("o") is None:
        return False

    line = json.dumps({
        "_ticker": ticker, "t": bar.get("t"),
        "o": bar.get("o"), "h": bar.get("h"), "l": bar.get("l"),
        "c": bar.get("c"), "v": bar.get("v"),
        "vw": bar.get("vw"), "n": bar.get("n")
    }) + "\n"

    with write_lock:
        with open(ndjson_path, "a") as f:
            f.write(line)
    return True


def pull_fallback_parallel(client: PolygonClient, tickers: list[str],
                           target_date: str, ndjson_path: Path) -> tuple[int, int, int]:
    """
    Pull per-ticker daily bars in parallel. Writes NDJSON as results arrive.
    Returns (succeeded, failed, remaining_after_timeout).

    Gracefully handles as_completed() TimeoutError — collects partial results
    instead of crashing.  The outer shell timeout (45 min) is the hard kill;
    this inner timeout (10 min / 600s of silence) is the early-exit fuse for
    a hung Polygon API that trickle-feeds bytes to defeat socket timeouts.
    """
    write_lock = threading.Lock()
    last_call = [0.0]
    throttle_lock = threading.Lock()
    succeeded = [0]
    failed = [0]
    stats_lock = threading.Lock()
    t0 = time.time()

    def pull_one(t):
        with throttle_lock:
            elapsed = time.time() - last_call[0]
            if elapsed < CALL_GAP:
                time.sleep(CALL_GAP - elapsed)
            last_call[0] = time.time()

        ok = pull_ticker_daily(client, t, target_date, ndjson_path, write_lock)
        with stats_lock:
            if ok:
                succeeded[0] += 1
                n = succeeded[0]
                if n % 500 == 0:
                    elapsed = time.time() - t0
                    print(f"    [{n:,}/{len(tickers):,}] {n/elapsed:.0f} tkr/s — {failed[0]} fail")
            else:
                failed[0] += 1

    processed: set[str] = set()
    remaining = 0
    timed_out = False

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS_FALLBACK)
    futures = {pool.submit(pull_one, t): t for t in tickers}
    try:
        try:
            for f in as_completed(futures, timeout=600):
                t = futures[f]
                processed.add(t)
                try:
                    f.result(timeout=60)
                except Exception:
                    with stats_lock:
                        failed[0] += 1
        except TimeoutError:
            timed_out = True
            remaining = len(tickers) - len(processed)
            elapsed = time.time() - t0
            print(f"\n  ⚠ as_completed() timeout after {elapsed:.0f}s — "
                  f"collected {len(processed):,}/{len(tickers):,} tickers, "
                  f"{remaining:,} remaining (API likely hung/trickling)")
            # Do NOT use ThreadPoolExecutor as a context manager here: __exit__
            # calls shutdown(wait=True), which waits forever on hung/trickling
            # urllib calls and defeats this inner fuse.  The shell-level
            # `timeout --signal=KILL` remains the hard process backstop.
            for f in futures:
                if futures[f] not in processed:
                    f.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Log incomplete tickers so the next run can pick them up
    if timed_out and remaining > 0:
        incomplete = sorted(set(tickers) - processed)
        if len(incomplete) <= 20:
            print(f"  Incomplete: {', '.join(incomplete)}")
        else:
            print(f"  Incomplete (first 20): {', '.join(incomplete[:20])}... "
                  f"({len(incomplete)} total)")

    return succeeded[0], failed[0], remaining


# ── Bulk insert NDJSON → DuckDB ───────────────────────────────────────────

def bulk_insert_daily(ndjson_path: Path) -> int:
    """Read daily NDJSON and merge into DuckDB. Returns rows inserted."""
    import duckdb
    if not ndjson_path.exists() or ndjson_path.stat().st_size == 0:
        return 0

    size_mb = ndjson_path.stat().st_size / 1024**2
    print(f"  Bulk merging {size_mb:.0f}MB NDJSON → DuckDB...")
    t0 = time.time()

    db = duckdb.connect(str(DB_PATH))
    # PRIMARY KEY (ticker, timestamp) already provides the covering index needed
    # for INSERT WHERE NOT EXISTS anti-joins — no separate index required.
    before_count = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]

    # Stage first so the merge can be transactional and can compare by UTC
    # market date. Polygon's per-ticker daily bars anchor at 04:00 UTC,
    # grouped daily bars anchor at 20:00 UTC, and hourly-derived provisional
    # bars anchor at 00:00 UTC. All three represent the same US market date in
    # UTC even though their intraday timestamps differ.
    db.execute(f"""
        CREATE TEMP TABLE stage_daily_append AS
        SELECT _ticker AS ticker,
               t AS timestamp,
               o AS open,
               h AS high,
               l AS low,
               c AS close,
               v AS volume,
               vw AS vwap,
               n AS transactions,
               epoch_ms(t::BIGINT)::DATE AS market_date
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY _ticker, epoch_ms(t::BIGINT)::DATE
                       ORDER BY CASE WHEN n IS NULL THEN 1 ELSE 0 END, t
                   ) AS rn
            FROM read_json('{ndjson_path}', format='newline_delimited',
                           columns={{_ticker: 'VARCHAR', t: 'BIGINT', o: 'DOUBLE',
                                     h: 'DOUBLE', l: 'DOUBLE', c: 'DOUBLE',
                                     v: 'DOUBLE', vw: 'DOUBLE', n: 'INTEGER'}})
            WHERE _ticker IS NOT NULL AND t IS NOT NULL
        ) j
        WHERE rn = 1
    """)

    db.execute("BEGIN TRANSACTION")
    try:
        # Official Polygon daily rows carry a transaction count. Hourly-derived
        # fallback rows intentionally leave transactions NULL. When official data
        # arrives later, replace the provisional derived row for the same ticker
        # and UTC market date instead of preserving a lower-quality duplicate.
        db.execute("""
            DELETE FROM daily_bars d
            USING stage_daily_append s
            WHERE d.ticker = s.ticker
              AND epoch_ms(d.timestamp::BIGINT)::DATE = s.market_date
              AND d.transactions IS NULL
              AND s.transactions IS NOT NULL
        """)

        db.execute("""
            INSERT INTO daily_bars (ticker, timestamp, open, high, low, close, volume, vwap, transactions)
            SELECT s.ticker, s.timestamp, s.open, s.high, s.low, s.close, s.volume, s.vwap, s.transactions
            FROM stage_daily_append s
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_bars d
                WHERE d.ticker = s.ticker
                  AND epoch_ms(d.timestamp::BIGINT)::DATE = s.market_date
            )
        """)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.execute("DROP TABLE IF EXISTS stage_daily_append")

    elapsed = time.time() - t0
    count = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    inserted = count - before_count
    tickers = db.execute("SELECT COUNT(DISTINCT ticker) FROM daily_bars").fetchone()[0]
    db.close()

    print(f"  Merge: {inserted:,} inserted, {count:,} total rows, {tickers:,} tickers in {elapsed:.1f}s")
    return inserted


# ── Main ──────────────────────────────────────────────────────────────────

def append_daily(client, target_date, vti_set, ndjson_path):
    """Try grouped first, fall back to per-ticker. All output to NDJSON."""
    t0 = time.time()

    # Primary: grouped endpoint
    print(f"  Trying grouped daily: {target_date}")
    n = pull_grouped_to_ndjson(client, target_date, vti_set, ndjson_path)

    if n > 0:
        print(f"  Grouped: {n:,} VTI bars → NDJSON ({time.time()-t0:.0f}s)")
        return n

    # Fallback: per-ticker parallel
    print(f"  Grouped returned 0 bars — falling back to per-ticker")
    tickers = sorted(vti_set)
    ok, fail, remaining = pull_fallback_parallel(client, tickers, target_date, ndjson_path)

    elapsed = time.time() - t0
    if remaining > 0:
        print(f"  Fallback: {ok:,} found, {fail:,} missing, {remaining:,} incomplete — "
              f"NDJSON has partial data ({elapsed:.0f}s)")
    else:
        print(f"  Fallback: {ok:,} found, {fail:,} missing ({elapsed:.0f}s)")
    return ok


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Daily bars append v3 — NDJSON-first")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), default: last trading day")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--insert-only", action="store_true", help="Just merge existing NDJSON")
    args = parser.parse_args()

    RAW_DIR.mkdir(exist_ok=True)
    ndjson_path = RAW_DIR / "daily_append.ndjson"

    if args.insert_only:
        print("INSERT-ONLY mode")
        bulk_insert_daily(ndjson_path)
        if ndjson_path.exists():
            ndjson_path.unlink()
        return

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)
        if target.weekday() >= 5:
            target = target - timedelta(days=target.weekday() - 4)

    target_str = target.isoformat()

    if args.dry_run:
        print(f"DRY RUN — would append daily bars for {target_str}")
        print(f"  Primary: grouped endpoint (1 API call)")
        print(f"  Fallback: per-ticker ({len(load_vti_tickers()):,} calls, {MAX_WORKERS_FALLBACK} workers)")
        return

    print(f"DAILY APPEND v3 — {target_str}")
    vti_set = load_vti_tickers()
    print(f"  Tickers: {len(vti_set):,}")

    # Step 1: Insert any leftover NDJSON from previous crashed run
    if ndjson_path.exists() and ndjson_path.stat().st_size > 0:
        print("  Found leftover NDJSON — inserting first...")
        bulk_insert_daily(ndjson_path)
        ndjson_path.unlink()

    # Step 2: Pull → NDJSON (NO DB writes)
    client = PolygonClient(timeout=API_TIMEOUT, retries=3)
    total = append_daily(client, target_str, vti_set, ndjson_path)

    # Step 3: Bulk insert NDJSON → DuckDB
    if ndjson_path.exists() and ndjson_path.stat().st_size > 0:
        bulk_insert_daily(ndjson_path)
        ndjson_path.unlink()

    # Final stats
    import duckdb
    db = duckdb.connect(str(DB_PATH))
    count = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    db_tickers = db.execute("SELECT COUNT(DISTINCT ticker) FROM daily_bars").fetchone()[0]
    db.close()

    print(f"\n  ✅ daily_bars: {count:,} rows, {db_tickers:,} tickers")
    if total == 0:
        print(f"  ⚠ No new data — may be weekend/holiday or Polygon lag")


if __name__ == "__main__":
    main()
