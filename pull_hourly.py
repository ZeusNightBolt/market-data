#!/usr/bin/env python3
"""
Hourly Bars Pull v5 — Year-Chunked Concurrency
================================================
Polygon server-side query execution is the bottleneck — 5-year range
scans take 10-30s and Polygon's queue handles only 2-3 concurrently.
1-year scans take 2-5s and allow 6-8 concurrent workers.

v5 changes (May 2026):
  - Year-chunking: split 5yr range into annual segments → 5× API calls
  - Incomplete detection: compare hourly bar count vs daily baseline
  - Higher concurrency: 6 workers (vs 2), no inter-call throttle
  - Expected: ~0.30 tkr/s → ~2.5 hrs for 2,700 incomplete tickers

Usage:
  python3 pull_hourly.py              # pull incomplete → NDJSON → DB
  python3 pull_hourly.py --insert-only  # just merge existing NDJSON
"""

import os, sys, json, time, logging, threading, queue
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
RAW_DIR = BASE_DIR / "raw"
CKPT_PATH = BASE_DIR / "hourly_checkpoint.json"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

HISTORY_START = "2021-06-01"
HISTORY_END = date.today().isoformat()
MAX_WORKERS = 6             # v5: 6 workers for 1yr chunks (was 2 for 5yr)
API_TIMEOUT = 20            # 1yr scans take 2-5s; 20s is generous
TICKER_TIMEOUT = 120        # per-future timeout (covers 5 chunks × 20s)
RETRIES = 0                 # these never 429 — just slow. don't retry.
RECENT_GAP_HOURS = 1        # flag tickers where latest daily close is newer than latest hourly by >1h

# Module-level: maps ticker → "YYYY-MM-DD" start date for recent-gap pull
# Populated by get_incomplete_tickers(), consumed by generate_chunks()
RECENT_GAP_TICKERS: dict[str, str] = {}

sys.path.insert(0, str(BASE_DIR))
from polygon_client import PolygonClient

client = PolygonClient(timeout=API_TIMEOUT, retries=RETRIES)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("pull_hourly")

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


def get_incomplete_tickers() -> list[str]:
    """Return VTI tickers with incomplete hourly coverage.

    Checks per-year date range coverage: for each year a ticker has daily data,
    verifies that hourly bars cover the full year (through mid-December at least).
    This catches pagination-truncated data regardless of bar count — a ticker
    with data stopping at Jul 2023 is incomplete even if it has 7,000 total bars.
    """
    import duckdb

    all_vti = sorted(set(load_vti_tickers()))
    incomplete = set()

    try:
        db = duckdb.connect(str(DB_PATH))

        # Per-year coverage check: for each (ticker, year) where daily bars exist,
        # verify hourly bars extend through at least Dec 15 of that year.
        # A year where hourly stops mid-year = pagination truncation = incomplete.
        rows = db.execute("""
            WITH years AS (
                SELECT DISTINCT ticker, year(epoch_ms(timestamp)) as yr
                FROM daily_bars
                WHERE year(epoch_ms(timestamp)) BETWEEN 2021 AND 2026
                  AND year(epoch_ms(timestamp)) < year(current_date)  -- exclude current (incomplete) year
            ),
            hourly_max AS (
                SELECT ticker, year(epoch_ms(timestamp)) as yr,
                       MAX(timestamp) as max_ts
                FROM hourly_bars
                WHERE year(epoch_ms(timestamp)) BETWEEN 2021 AND 2026
                GROUP BY ticker, yr
            )
            SELECT y.ticker, y.yr,
                   COALESCE(
                       month(epoch_ms(h.max_ts)),
                       0
                   ) as last_month
            FROM years y
            LEFT JOIN hourly_max h ON y.ticker = h.ticker AND y.yr = h.yr
            WHERE COALESCE(month(epoch_ms(h.max_ts)), 0) < 11  -- incomplete if not reaching November
               OR h.max_ts IS NULL
            ORDER BY y.ticker, y.yr
        """).fetchall()

        db.close()

        for ticker, yr, last_month in rows:
            if ticker in all_vti:
                incomplete.add(ticker)

        # Also include VTI tickers with zero hourly bars at all
        hourly_tickers = set()
        try:
            db2 = duckdb.connect(str(DB_PATH))
            hourly_tickers = set(r[0] for r in db2.execute(
                "SELECT DISTINCT ticker FROM hourly_bars").fetchall())
            db2.close()
        except Exception:
            pass

        for t in all_vti:
            if t not in hourly_tickers:
                incomplete.add(t)

        # ── Recent gap detection ──
        # Tickers where daily data is ahead of hourly data in the recent period.
        # This must be sub-day, not multi-day: Polygon can have the official daily
        # close while hourly bars for that ticker still stop in the morning.  CELH
        # showed this on 2026-06-16: daily close $30.01 but hourly stopped at 09:00.
        # The per-year check above excludes the current year; this catches that gap.
        RECENT_GAP_TICKERS.clear()
        try:
            db3 = duckdb.connect(str(DB_PATH))
            recent_rows = db3.execute(f"""
                WITH latest AS (
                    SELECT ticker,
                           MAX(timestamp) AS max_daily,
                           COALESCE(
                               (SELECT MAX(h.timestamp) FROM hourly_bars h
                                WHERE h.ticker = d.ticker), 0
                           ) AS max_hourly
                    FROM daily_bars d
                    GROUP BY ticker
                )
                SELECT ticker, max_daily, max_hourly
                FROM latest
                WHERE max_daily > max_hourly + ({RECENT_GAP_HOURS} * 3600000)
                  AND max_hourly > 0  -- ticker has SOME hourly data
                ORDER BY ticker
            """).fetchall()
            db3.close()
            for ticker, max_daily, max_hourly in recent_rows:
                if ticker in all_vti:
                    # Start from the day after the last hourly bar
                    from datetime import datetime, timezone
                    start_ts = datetime.fromtimestamp(max_hourly / 1000, tz=timezone.utc)
                    start_date = start_ts.strftime("%Y-%m-%d")
                    RECENT_GAP_TICKERS[ticker] = start_date
                    incomplete.add(ticker)
            if recent_rows:
                log.info(f"Recent gap tickers (daily > hourly + {RECENT_GAP_HOURS}h): {len(recent_rows):,}")
        except Exception as e:
            log.warning(f"Recent gap detection failed ({e}) — skipping")

        incomplete_list = sorted(incomplete)
        log.info(f"VTI: {len(all_vti):,} | Complete: {len(all_vti) - len(incomplete_list):,} | Incomplete (year-gap check): {len(incomplete_list):,}")
        return incomplete_list

    except Exception as e:
        log.warning(f"DB query failed ({e}) — pulling all {len(all_vti):,} VTI tickers")
        return all_vti


# ── Year Chunk Generation ─────────────────────────────────────────────────

def generate_chunks(ticker: str) -> list[tuple[str, str, str]]:
    """Split ticker's date range into yearly chunks.

    Returns list of (ticker, start_iso, end_iso) tuples.
    Handles partial first year (starts June 2021) and partial last year (ends today).

    If ticker is in RECENT_GAP_TICKERS (daily > hourly in the current year),
    generates only the recent gap range instead of full 2021-present history.
    This avoids re-pulling years of already-complete historical data.
    """
    today = date.today()

    # Recent-gap shortcut: only pull the missing recent period
    if ticker in RECENT_GAP_TICKERS:
        gap_start = date.fromisoformat(RECENT_GAP_TICKERS[ticker])
        if gap_start < today:
            return [(ticker, gap_start.isoformat(), today.isoformat())]
        return []  # nothing to pull

    chunks = []
    start = date(2021, 6, 1)

    year = start.year
    while year <= today.year:
        chunk_start = date(year, 6, 1) if year == 2021 else date(year, 1, 1)
        chunk_end = today if year == today.year else date(year, 12, 31)

        if chunk_start < chunk_end:
            chunks.append((ticker, chunk_start.isoformat(), chunk_end.isoformat()))

        year += 1

    return chunks


# ── Core: Pull one chunk → list of row tuples (with pagination) ───────────

def pull_hourly_chunk(ticker: str, start_date: str, end_date: str) -> list[tuple]:
    """Pull ALL hourly bars for a ticker over a date range.

    Follows next_url pagination. Polygon aggs paginate by advancing the
    start timestamp in the URL path (not via cursor tokens). The next_url
    includes the updated date range — we must use it as-is.
    """
    import urllib.parse

    # URL-encode ticker for slash tickers (BRK.A → BRK%2FA)
    encoded_ticker = urllib.parse.quote(ticker, safe='')
    base_path = f"/v2/aggs/ticker/{encoded_ticker}/range/1/hour/{start_date}/{end_date}"

    all_results = []
    next_url = None
    pages = 0
    MAX_PAGES = 10  # safety limit

    while pages < MAX_PAGES:
        if next_url:
            # Follow the full next_url — it contains the updated date range
            parsed = urllib.parse.urlparse(next_url)
            path = parsed.path
            # Extract query params (cursor + anything else), skip apiKey
            params = {}
            for k, v in urllib.parse.parse_qs(parsed.query).items():
                if k != "apiKey":
                    params[k] = v[0]
        else:
            path = base_path
            params = {"limit": 50000, "sort": "asc"}

        resp = client._get(path, params)

        # Aggs endpoint doesn't return "status" on success.
        # Error responses have "status": "ERROR".
        if not isinstance(resp, dict) or resp.get("status") == "ERROR":
            break

        results = resp.get("results", [])
        if not results:
            break

        all_results.extend(results)
        pages += 1

        # Check for next page
        next_url = resp.get("next_url")
        if not next_url:
            break

    if not all_results:
        return []

    # Parse bars into row tuples
    rows = []
    for bar in all_results:
        rows.append((
            ticker, bar["t"],
            bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"),
            bar.get("v"), bar.get("vw"), bar.get("n")
        ))
    return rows


# ── Phase 1: Pull ALL chunks → writer thread drains to NDJSON ─────────────

def pull_all(tickers: list[str]):
    """Pull all incomplete tickers via year-chunked parallel execution.

    Each ticker is split into yearly API calls. All chunks compete in a single
    ThreadPoolExecutor. Results stream to NDJSON via writer thread.
    """
    if not tickers:
        log.info("Hourly: all tickers complete")
        return

    # Build chunk list
    all_chunks = []
    for t in tickers:
        all_chunks.extend(generate_chunks(t))

    log.info(f"Incomplete tickers: {len(tickers):,} | Year chunks: {len(all_chunks):,}")
    log.info(f"Config: {MAX_WORKERS} workers | {API_TIMEOUT}s timeout | " +
             f"~{len(all_chunks) / MAX_WORKERS * 4:.0f}s estimated " +
             f"(~{len(all_chunks) / MAX_WORKERS * 4 / 60:.0f} min)")

    t0 = time.time()

    # Shared state
    row_queue = queue.Queue()
    stats = {"attempted": 0, "succeeded": 0, "failed": 0, "rows": 0, "locked": threading.Lock()}
    ticker_chunks_done = {}  # ticker → count of completed chunks

    # NDJSON writer thread
    ndjson_path = RAW_DIR / "hourly.ndjson"
    if ndjson_path.exists():
        ndjson_path.unlink()

    def writer():
        rows_written = 0
        with open(ndjson_path, "a") as f:
            while True:
                try:
                    batch = row_queue.get(timeout=5)
                    if batch is None:  # sentinel
                        break
                    lines = []
                    for row in batch:
                        lines.append(json.dumps({
                            "_ticker": row[0], "t": row[1],
                            "o": row[2], "h": row[3], "l": row[4], "c": row[5],
                            "v": row[6], "vw": row[7], "n": row[8]
                        }) + "\n")
                    f.writelines(lines)
                    f.flush()
                    rows_written += len(batch)
                    row_queue.task_done()
                except queue.Empty:
                    continue
        log.info(f"Writer: {rows_written:,} rows flushed to disk")

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()

    def pull_one_chunk(ticker, start_date, end_date):
        rows = pull_hourly_chunk(ticker, start_date, end_date)
        success = len(rows) > 0

        if success:
            row_queue.put(rows)

        with stats["locked"]:
            stats["attempted"] += 1
            if success:
                stats["succeeded"] += 1
                stats["rows"] += len(rows)
            else:
                stats["failed"] += 1

            # Per-ticker progress tracking
            ticker_chunks_done[ticker] = ticker_chunks_done.get(ticker, 0) + 1

            # Log every 500 chunk completions
            if stats["attempted"] % 500 == 0:
                elapsed = time.time() - t0
                complete_tickers = sum(
                    1 for t in ticker_chunks_done
                    if ticker_chunks_done[t] >= len(generate_chunks(t))
                )
                rate = stats["succeeded"] / elapsed if elapsed > 0 else 0
                pct = complete_tickers / len(tickers) * 100
                log.info(f"  [{complete_tickers:,}/{len(tickers):,}] tickers {pct:.0f}% — "
                         f"{stats['succeeded']:,}/{stats['attempted']:,} chunks "
                         f"({rate:.1f} chunks/s) — {stats['rows']:,} rows — {stats['failed']} fail")

        return success

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for ticker, start_date, end_date in all_chunks:
            f = pool.submit(pull_one_chunk, ticker, start_date, end_date)
            futures[f] = (ticker, start_date, end_date)

        pool_deadline = TICKER_TIMEOUT * max(1, len(all_chunks) // MAX_WORKERS)
        for f in as_completed(futures, timeout=pool_deadline):
            ticker, start, end = futures[f]
            try:
                f.result(timeout=TICKER_TIMEOUT)
            except Exception as e:
                with stats["locked"]:
                    stats["failed"] += 1
                    stats["attempted"] += 1
                log.debug(f"  Chunk failed: {ticker} {start}→{end}: {e}")

    # Signal writer to stop
    row_queue.put(None)
    writer_thread.join(timeout=120)

    elapsed = time.time() - t0
    complete_tickers = sum(
        1 for t in ticker_chunks_done
        if ticker_chunks_done[t] >= len(generate_chunks(t))
    )
    log.info(f"Pull done: {complete_tickers:,}/{len(tickers):,} tickers complete, "
             f"{stats['succeeded']:,}/{stats['attempted']:,} chunks succeeded, "
             f"{stats['rows']:,} rows in {elapsed:.0f}s "
             f"({stats['succeeded']/elapsed:.2f} chunks/s)")

    return stats["rows"]


# ── Phase 2: Bulk merge NDJSON → DuckDB ──────────────────────────────────

def bulk_insert_ndjson():
    import duckdb
    path = RAW_DIR / "hourly.ndjson"
    if not path.exists():
        log.info("No hourly.ndjson — nothing to insert")
        return 0

    size_mb = path.stat().st_size / 1024**2
    log.info(f"Bulk merging {size_mb:.0f}MB NDJSON → DuckDB...")
    t0 = time.time()

    db = duckdb.connect(str(DB_PATH))

    # Ensure index exists for fast NOT EXISTS check
    db.execute("CREATE INDEX IF NOT EXISTS idx_hourly_ticker_ts ON hourly_bars(ticker, timestamp)")

    # Pure SQL merge
    db.execute(f"""
        INSERT INTO hourly_bars
        SELECT j._ticker, j.t, j.o, j.h, j.l, j.c, j.v, j.vw, j.n
        FROM read_json('{path}', format='newline_delimited',
                       columns={{_ticker: 'VARCHAR', t: 'BIGINT', o: 'DOUBLE',
                                 h: 'DOUBLE', l: 'DOUBLE', c: 'DOUBLE',
                                 v: 'DOUBLE', vw: 'DOUBLE', n: 'INTEGER'}}) j
        WHERE NOT EXISTS (
            SELECT 1 FROM hourly_bars h
            WHERE h.ticker = j._ticker AND h.timestamp = j.t
        )
    """)

    elapsed = time.time() - t0
    count = db.execute("SELECT COUNT(*) FROM hourly_bars").fetchone()[0]
    tickers = db.execute("SELECT COUNT(DISTINCT ticker) FROM hourly_bars").fetchone()[0]
    db.close()

    log.info(f"  Merge: {count:,} total rows, {tickers:,} tickers in {elapsed:.1f}s")
    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Hourly bars pull v5 — year-chunked concurrency")
    parser.add_argument("--insert-only", action="store_true", help="Skip pull, just merge NDJSON")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pulled, exit")
    args = parser.parse_args()

    RAW_DIR.mkdir(exist_ok=True)

    if args.insert_only:
        log.info("INSERT-ONLY mode")
        bulk_insert_ndjson()
        return

    # Step 1: Insert any leftover NDJSON from a previous crashed run
    ndjson_path = RAW_DIR / "hourly.ndjson"
    if ndjson_path.exists() and ndjson_path.stat().st_size > 0:
        log.info("Found leftover NDJSON from previous run — inserting first...")
        bulk_insert_ndjson()
        ndjson_path.unlink()  # consumed — clean slate

    # Step 2: Get tickers with incomplete hourly coverage
    tickers = get_incomplete_tickers()

    if args.dry_run:
        chunks = sum(len(generate_chunks(t)) for t in tickers)
        log.info(f"DRY RUN: {len(tickers):,} incomplete tickers → {chunks:,} year chunks")
        log.info(f"  Est. time: ~{chunks / MAX_WORKERS * 4 / 60:.0f} min at {MAX_WORKERS} workers")
        # Show first 20
        for t in tickers[:20]:
            import duckdb
            db = duckdb.connect(str(DB_PATH))
            hc = db.execute("SELECT COUNT(*) FROM hourly_bars WHERE ticker=?", [t]).fetchone()[0]
            dc = db.execute("SELECT COUNT(*) FROM daily_bars WHERE ticker=?", [t]).fetchone()[0]
            db.close()
            log.info(f"  {t}: {hc:,} hourly / {dc:,} daily ({hc/(dc*7)*100 if dc > 0 else 0:.0f}% of max)")
        if len(tickers) > 20:
            log.info(f"  ... and {len(tickers)-20:,} more")
        return

    if not tickers:
        log.info("All VTI tickers have complete hourly data ✓")
        return

    # Step 3: Pull all incomplete tickers (year-chunked) into fresh NDJSON
    total_rows = pull_all(tickers)

    # Step 4: Bulk insert
    if total_rows > 0:
        bulk_insert_ndjson()
        ndjson_path.unlink()  # consumed

    log.info("✅ Hourly pull complete")


if __name__ == "__main__":
    main()
