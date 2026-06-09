#!/usr/bin/env python3
"""
Ticker Details Pull — Resume-Safe with Bulk Insert
====================================================
Pulls ticker details (name, market_cap, exchange, SIC, employees, etc.)
for all VTI tickers missing from DB. One API call per ticker.

Polygon `/v3/reference/tickers/{T}` latency: ~12-15s on Monday.
Strategy: 2 workers, 1.5s gap, 100% reliability over speed.

Usage:
  python3 pull_ticker_details.py              # pull missing
  python3 pull_ticker_details.py --insert-only  # just merge existing NDJSON
"""

import os, sys, json, time, logging, threading, queue
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

# ── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
RAW_DIR = BASE_DIR / "raw"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

MAX_WORKERS = 2
INTER_CALL_GAP = 1.5
API_TIMEOUT = 30
TICKER_TIMEOUT = 60
RETRIES = 3

sys.path.insert(0, str(BASE_DIR))
from polygon_client import PolygonClient

client = PolygonClient(timeout=API_TIMEOUT, retries=RETRIES)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("ticker_details")

# ── Ticker Loading ────────────────────────────────────────────────────────

def load_vti_tickers() -> list[str]:
    tickers = []
    with open(SECTOR_MAP) as f:
        next(f)
        for line in f:
            t = line.split(",")[0].strip().upper()
            if t:
                tickers.append(t)
    return tickers


def get_missing_tickers(stale_days: int | None = None, all_tickers: bool = False) -> list[str]:
    """Return VTI tickers to refresh. Falls back to full list if DB locked.

    Default behavior is missing-only. For daily refresh jobs, pass
    stale_days=7 so existing point-in-time fields such as market_cap,
    employees, shares_outstanding, and exchange metadata are refreshed on a weekly
    cadence without trying to repull thousands of tickers every weekday.
    """
    import duckdb
    all_vti = sorted(set(load_vti_tickers()))

    if all_tickers:
        log.info(f"VTI: {len(all_vti):,} | Refresh mode: all tickers")
        return all_vti

    # Try DB query first
    try:
        db = duckdb.connect(str(DB_PATH))
        have = set(r[0] for r in db.execute("SELECT DISTINCT ticker FROM ticker_details").fetchall())
        missing = [t for t in all_vti if t not in have]

        stale = []
        if stale_days is not None:
            cutoff_ms = int((time.time() - stale_days * 86400) * 1000)
            stale = [r[0] for r in db.execute("""
                SELECT ticker FROM ticker_details
                WHERE last_updated IS NULL OR last_updated < ?
            """, [cutoff_ms]).fetchall() if r[0] in all_vti]

        db.close()
        todo = sorted(set(missing) | set(stale))
        mode = f"missing + stale>{stale_days}d" if stale_days is not None else "missing only"
        log.info(f"VTI: {len(all_vti):,} | In DB: {len(have):,} | Missing: {len(missing):,} | Stale: {len(stale):,} | To pull: {len(todo):,} ({mode})")
        return todo
    except Exception as e:
        log.warning(f"DB locked — pulling all {len(all_vti):,} VTI tickers (INSERT OR REPLACE will dedup)")
        return all_vti


# ── Core: Pull one ticker ─────────────────────────────────────────────────

def pull_ticker_detail(ticker: str) -> tuple | None:
    """Pull ticker details. Returns (ticker, name, mcap, exchange, sic_code,
    sic_desc, employees, shares_out, list_date, currency, last_updated) or None."""
    resp = client._get(f"/v3/reference/tickers/{ticker}")
    if not client._ok(resp):
        return None
    r = resp.get("results", {})
    if not r:
        return None
    return (
        ticker,
        r.get("name"),
        r.get("market_cap"),
        r.get("primary_exchange"),
        r.get("sic_code"),
        r.get("sic_description"),
        r.get("total_employees"),
        r.get("weighted_shares_outstanding"),
        r.get("list_date"),
        r.get("currency_name"),
        int(time.time() * 1000)
    )


# ── Pull all → NDJSON → DuckDB ────────────────────────────────────────────

def pull_all(tickers: list[str]):
    remaining = list(tickers)
    if not remaining:
        log.info("Ticker details: already complete")
        return 0

    log.info(f"Pulling {len(remaining):,} tickers | {MAX_WORKERS} workers | {API_TIMEOUT}s timeout")
    est_sec = len(remaining) / (MAX_WORKERS / 25.0)
    log.info(f"Expected: ~{est_sec:.0f}s (~{est_sec/60:.0f} min)")
    t0 = time.time()

    row_queue = queue.Queue()
    stats = {"attempted": 0, "succeeded": 0, "failed": 0, "locked": threading.Lock()}
    last_call = [0.0]
    throttle_lock = threading.Lock()

    # NDJSON writer — file lifecycle is managed by main(): it inserts any
    # leftover NDJSON before calling pull_all() and cleans up after insert.
    ndjson_path = RAW_DIR / "ticker_details.ndjson"
    # Only delete if the file is from a stale/abandoned run (zero bytes).
    # Otherwise main() already handled it and we should append.
    if ndjson_path.exists() and ndjson_path.stat().st_size == 0:
        ndjson_path.unlink()

    def writer():
        written = 0
        with open(ndjson_path, "a") as f:
            while True:
                try:
                    batch = row_queue.get(timeout=3)
                    if batch is None:
                        break
                    for row in batch:
                        f.write(json.dumps({
                            "ticker": row[0], "name": row[1], "market_cap": row[2],
                            "exchange": row[3], "sic_code": row[4], "sic_description": row[5],
                            "employees": row[6], "shares_outstanding": row[7],
                            "list_date": row[8], "currency": row[9], "last_updated": row[10]
                        }) + "\n")
                    f.flush()
                    written += len(batch)
                    row_queue.task_done()
                except queue.Empty:
                    continue
        log.info(f"Writer: {written:,} tickers flushed to disk")

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()

    def pull_one(ticker):
        with throttle_lock:
            elapsed = time.time() - last_call[0]
            if elapsed < INTER_CALL_GAP:
                time.sleep(INTER_CALL_GAP - elapsed)
            last_call[0] = time.time()

        row = pull_ticker_detail(ticker)
        success = row is not None
        if success:
            row_queue.put([row])
            with stats["locked"]:
                stats["succeeded"] += 1
        else:
            with stats["locked"]:
                stats["failed"] += 1

        with stats["locked"]:
            stats["attempted"] += 1
            n = stats["succeeded"]
            if n > 0 and n % 200 == 0:
                elapsed = time.time() - t0
                rate = n / elapsed if elapsed > 0 else 0
                pct = n / len(remaining) * 100
                log.info(f"  [{n:,}/{len(remaining):,}] {pct:.0f}% — {rate:.1f} tkr/s — {stats['failed']} fail")

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {pool.submit(pull_one, t): t for t in remaining}
    processed: set[str] = set()
    try:
        try:
            for f in as_completed(futures, timeout=TICKER_TIMEOUT * max(1, len(remaining) // MAX_WORKERS)):
                processed.add(futures[f])
                try:
                    f.result(timeout=TICKER_TIMEOUT)
                except Exception:
                    with stats["locked"]:
                        stats["failed"] += 1
                        stats["attempted"] += 1
        except TimeoutError:
            remaining_unfinished = sorted(set(remaining) - processed)
            log.warning(
                f"as_completed() timeout — collected {len(processed):,}/{len(remaining):,}; "
                f"{len(remaining_unfinished):,} unfinished. Partial NDJSON will be merged."
            )
            for f, t in futures.items():
                if t not in processed:
                    f.cancel()
    finally:
        # Avoid ThreadPoolExecutor.__exit__/shutdown(wait=True), which can hang
        # forever on trickling urllib reads. The shell-level timeout remains the
        # hard process backstop.
        pool.shutdown(wait=False, cancel_futures=True)

    row_queue.put(None)
    writer_thread.join(timeout=60)

    elapsed = time.time() - t0
    log.info(f"Pull done: {stats['succeeded']:,} succeeded, {stats['failed']:,} failed "
             f"in {elapsed:.0f}s ({stats['succeeded']/elapsed:.2f} tkr/s)")
    return stats["succeeded"]


# ── Bulk insert NDJSON → DuckDB ───────────────────────────────────────────

def bulk_insert_ndjson():
    import duckdb
    path = RAW_DIR / "ticker_details.ndjson"
    if not path.exists():
        log.info("No ticker_details.ndjson — nothing to insert")
        return 0

    size_mb = path.stat().st_size / 1024**2
    log.info(f"Bulk merging {size_mb:.0f}MB NDJSON → DuckDB...")
    t0 = time.time()

    db = duckdb.connect(str(DB_PATH))

    db.execute(f"""
        INSERT OR REPLACE INTO ticker_details
        SELECT j.ticker, j.name, j.market_cap, j.exchange, j.sic_code,
               j.sic_description, j.employees, j.shares_outstanding,
               j.list_date, j.currency, j.last_updated
        FROM read_json('{path}', format='newline_delimited',
                       columns={{ticker: 'VARCHAR', name: 'VARCHAR', market_cap: 'DOUBLE',
                                 exchange: 'VARCHAR', sic_code: 'VARCHAR', sic_description: 'VARCHAR',
                                 employees: 'INTEGER', shares_outstanding: 'DOUBLE',
                                 list_date: 'VARCHAR', currency: 'VARCHAR', last_updated: 'BIGINT'}}) j
    """)

    elapsed = time.time() - t0
    count = db.execute("SELECT COUNT(*) FROM ticker_details").fetchone()[0]
    db.close()
    log.info(f"  Merge: {count:,} ticker_details rows in {elapsed:.1f}s")
    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ticker details pull")
    parser.add_argument("--insert-only", action="store_true", help="Skip pull, just merge NDJSON")
    parser.add_argument("--stale-days", type=int, default=None,
                        help="Also refresh tickers whose last_updated is older than N days")
    parser.add_argument("--all", action="store_true",
                        help="Refresh all VTI tickers, not just missing/stale")
    args = parser.parse_args()

    RAW_DIR.mkdir(exist_ok=True)

    if args.insert_only:
        log.info("INSERT-ONLY mode")
        ndjson_path = RAW_DIR / "ticker_details.ndjson"
        bulk_insert_ndjson()
        if ndjson_path.exists():
            ndjson_path.unlink()
        return

    # Step 1: Insert any leftover NDJSON from crash
    ndjson_path = RAW_DIR / "ticker_details.ndjson"
    if ndjson_path.exists() and ndjson_path.stat().st_size > 0:
        log.info("Found leftover NDJSON — inserting first...")
        bulk_insert_ndjson()
        ndjson_path.unlink()

    # Step 2: Get missing/stale
    tickers = get_missing_tickers(stale_days=args.stale_days, all_tickers=args.all)
    if not tickers:
        log.info("All VTI tickers have details ✓")
        return

    # Step 3: Pull
    total = pull_all(tickers)

    # Step 4: Insert
    if total > 0:
        bulk_insert_ndjson()
        ndjson_path.unlink()

    log.info("✅ Ticker details pull complete")


if __name__ == "__main__":
    main()
