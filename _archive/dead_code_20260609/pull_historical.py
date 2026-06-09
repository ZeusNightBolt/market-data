#!/usr/bin/env python3
"""
Market Data Warehouse — Historical Pull (FIXED)
================================================
Pulls 5 years of OHLCV (hourly, daily, weekly) + dividends + splits +
ticker details for all VTI constituents from Polygon.io.
Stores in DuckDB at ~/market-data/market_data.duckdb.

Resume-safe: checkpoint file tracks progress.
Daily bars already complete (441K rows). Hourly has partial progress.

FIXES vs v1:
  - Loads tickers from sector_map_vti.csv (3,354 tickers, not 361)
  - as_completed timeout to prevent hangs
  - Per-worker sleep-based rate limiting (simpler, no token bucket bugs)
  - Log file (~/market-data/pull.log) for progress even when stdout buffered
"""

import os, sys, json, time, logging, signal
import urllib.request, urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

# ── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
SCHEMA_PATH = BASE_DIR / "schema.sql"
CHECKPOINT_PATH = BASE_DIR / "checkpoint.json"
LOG_PATH = BASE_DIR / "pull.log"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

API_KEY = os.environ.get("POLYGON_API_KEY")
if not API_KEY:
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("POLYGON_API_KEY="):
                API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

BASE_URL = "https://api.polygon.io"
HISTORY_START = "2021-06-01"
HISTORY_END = date.today().isoformat()

# Rate limiting — 900 calls/min total, split across workers
RATE_LIMIT_CALLS = 900
RATE_LIMIT_WINDOW = 60.0
MAX_WORKERS = 4                 # fewer workers = less contention
CALL_TIMEOUT = 15               # seconds per HTTP call
TICKER_TIMEOUT = 300            # max seconds per ticker (futures timeout)

# Per-worker sleep between API calls to stay under rate limit
CALL_INTERVAL = (RATE_LIMIT_WINDOW / RATE_LIMIT_CALLS) * MAX_WORKERS

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


# ── API Helpers ───────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None, quiet: bool = False) -> dict:
    """Single API call with error handling."""
    if params is None:
        params = {}
    params["apiKey"] = API_KEY
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{query}"

    req = urllib.request.Request(url, headers={"User-Agent": "Hermes-MarketDB/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(2)
            return api_get(path, params, quiet=True)  # retry once on rate limit
        body = e.read().decode()[:200] if e.fp else ""
        if not quiet:
            log.warning(f"HTTP {e.code} on {path[:60]}: {body}")
        return {"status": "ERROR", "http_status": e.code}
    except Exception as e:
        if not quiet:
            log.warning(f"Network error on {path[:60]}: {e}")
        return {"status": "ERROR", "error": str(e)}


# ── Ticker Loading ────────────────────────────────────────────────────────

def load_tickers() -> list[str]:
    """Load VTI constituent tickers from sector_map CSV."""
    tickers = []
    with open(SECTOR_MAP) as f:
        next(f)  # skip header
        for line in f:
            t = line.split(",")[0].strip().upper()
            if t:
                tickers.append(t)
    log.info(f"Loaded {len(tickers):,} tickers from {SECTOR_MAP.name}")
    return tickers


# ── Checkpoint ────────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text())
    return {}


def save_checkpoint(cp: dict):
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.rename(CHECKPOINT_PATH)


# ── Database ──────────────────────────────────────────────────────────────

def init_database():
    import duckdb
    db = duckdb.connect(str(DB_PATH))
    if SCHEMA_PATH.exists():
        db.execute(SCHEMA_PATH.read_text())
    log.info(f"Database ready: {DB_PATH}")
    return db


# ── Phase 1: Daily Bars (Grouped Endpoint) ────────────────────────────────

def trading_days(start: str, end: str) -> list[str]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    days = []
    current = s
    while current <= e:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def pull_daily_all(db, tickers: list[str]):
    """Pull daily OHLCV for all US stocks via grouped endpoint."""
    cp = load_checkpoint()
    last_date = cp.get("daily_last_date")
    vti_set = set(tickers)

    days = trading_days(HISTORY_START, HISTORY_END)
    if last_date:
        days = [d for d in days if d > last_date]
        log.info(f"Daily: resuming from {days[0] if days else 'DONE'} ({len(days)} remaining)")
    else:
        log.info(f"Daily: pulling {len(days)} trading days")

    if not days:
        log.info("Daily: already complete")
        return

    total_bars = 0
    for i, day in enumerate(days):
        time.sleep(CALL_INTERVAL)  # per-worker rate limiting
        resp = api_get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}",
                       {"limit": 50000}, quiet=True)

        if resp.get("status") not in ("OK", "DELAYED"):
            continue

        results = resp.get("results", [])
        rows = []
        for bar in results:
            t = bar.get("T", "")
            if t in vti_set:
                rows.append((t, bar["t"], bar.get("o"), bar.get("h"),
                             bar.get("l"), bar.get("c"), bar.get("v"),
                             bar.get("vw"), bar.get("n")))

        if rows:
            db.executemany(
                """INSERT OR IGNORE INTO daily_bars
                   (ticker, timestamp, open, high, low, close, volume, vwap, transactions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
            total_bars += len(rows)

        if (i + 1) % 200 == 0:
            save_checkpoint({"daily_last_date": day})
            log.info(f"Daily [{i+1}/{len(days)}] {day}: {total_bars:,} bars")

    cp["daily_last_date"] = days[-1] if days else cp.get("daily_last_date")
    save_checkpoint(cp)
    log.info(f"Daily complete: {total_bars:,} bars across {len(days)} days")


# ── Phase 2-3: Per-Ticker Aggregates (Hourly, Weekly) ─────────────────────

def pull_aggregates(ticker: str, timespan: str) -> list[tuple]:
    """Pull ALL pages of bars for a ticker. Returns list of tuples."""
    path = f"/v2/aggs/ticker/{ticker}/range/1/{timespan}/{HISTORY_START}/{HISTORY_END}"
    params = {"limit": 50000, "sort": "asc"}
    all_bars = []
    url = f"{BASE_URL}{path}?apiKey={API_KEY}&limit=50000&sort=asc"

    while url and len(all_bars) < 100000:  # safety cap
        time.sleep(CALL_INTERVAL)  # per-worker rate limit
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-MarketDB/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            log.debug(f"{ticker} page error: {e}")
            break

        if data.get("status") not in ("OK", "DELAYED"):
            break

        for bar in data.get("results", []):
            all_bars.append((ticker, bar["t"], bar.get("o"), bar.get("h"),
                             bar.get("l"), bar.get("c"), bar.get("v"),
                             bar.get("vw"), bar.get("n")))

        next_url = data.get("next_url")
        url = f"{next_url}&apiKey={API_KEY}" if next_url else None

    return all_bars


def pull_timeframe(db, tickers: list[str], timespan: str, table_name: str):
    """Parallel pull for a given timespan — with timeout on futures."""
    cp = load_checkpoint()
    key = f"{timespan}_last_idx"
    start_idx = cp.get(key, 0)
    remaining = tickers[start_idx:]

    if not remaining:
        log.info(f"{timespan}: already complete")
        return

    log.info(f"{timespan}: {len(remaining):,} tickers (resume from idx {start_idx})")
    total_bars = 0
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for i, ticker in enumerate(remaining):
            f = pool.submit(pull_aggregates, ticker, timespan)
            futures[f] = (start_idx + i, ticker)

        for f in as_completed(futures, timeout=TICKER_TIMEOUT):
            idx, ticker = futures[f]
            try:
                bars = f.result(timeout=60)  # per-result timeout
                if bars:
                    db.executemany(
                        f"""INSERT OR IGNORE INTO {table_name}
                           (ticker, timestamp, open, high, low, close, volume, vwap, transactions)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", bars)
                    total_bars += len(bars)
            except FutureTimeout:
                log.warning(f"{ticker}: future timed out — skipping")
                failed += 1
            except Exception as e:
                log.debug(f"{ticker}: {e}")
                failed += 1

            completed += 1
            if completed % 50 == 0:
                pct = (completed / len(remaining)) * 100
                save_checkpoint({**cp, key: start_idx + completed})
                log.info(f"{timespan} [{completed}/{len(remaining)}] "
                         f"{pct:.0f}% — {total_bars:,} bars ({failed} failed)")

    cp[key] = len(tickers)
    save_checkpoint(cp)
    log.info(f"{timespan} complete: {total_bars:,} bars ({failed} failed)")


# ── Phase 4: Dividends & Splits ───────────────────────────────────────────

def pull_dividend(ticker: str) -> list[tuple]:
    time.sleep(CALL_INTERVAL)
    resp = api_get("/v3/reference/dividends", {"ticker": ticker, "limit": 1000}, quiet=True)
    rows = []
    for d in resp.get("results", []):
        rows.append((ticker, d.get("ex_dividend_date"), d.get("cash_amount"),
                     d.get("declaration_date"), d.get("pay_date"),
                     d.get("record_date"), d.get("frequency"),
                     d.get("dividend_type"), d.get("currency")))
    return rows


def pull_split(ticker: str) -> list[tuple]:
    time.sleep(CALL_INTERVAL)
    resp = api_get("/v3/reference/splits", {"ticker": ticker, "limit": 100}, quiet=True)
    rows = []
    for s in resp.get("results", []):
        rows.append((ticker, s.get("execution_date"),
                     s.get("split_from"), s.get("split_to")))
    return rows


def pull_corporate_actions(db, tickers: list[str]):
    cp = load_checkpoint()

    for action, key, func, table, columns in [
        ("Dividends", "dividends_last_idx", pull_dividend, "dividends",
         """INSERT OR IGNORE INTO dividends
            (ticker, ex_dividend_date, cash_amount, declaration_date,
             pay_date, record_date, frequency, dividend_type, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
        ("Splits", "splits_last_idx", pull_split, "splits",
         """INSERT OR IGNORE INTO splits
            (ticker, execution_date, split_from, split_to)
            VALUES (?, ?, ?, ?)"""),
    ]:
        start_idx = cp.get(key, 0)
        remaining = tickers[start_idx:]
        if not remaining:
            log.info(f"{action}: already complete")
            continue

        log.info(f"{action}: {len(remaining):,} tickers")
        total, done = 0, 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(func, t): (i, t)
                       for i, t in enumerate(remaining)}
            for f in as_completed(futures):
                rows = f.result()
                if rows:
                    db.executemany(table, rows)
                    total += len(rows)
                done += 1
                if done % 200 == 0:
                    save_checkpoint({**cp, key: start_idx + done})
                    log.info(f"{action} [{done}/{len(remaining)}] {total:,} records")

        cp[key] = len(tickers)
        save_checkpoint(cp)
        log.info(f"{action} complete: {total:,} records")


# ── Phase 5: Ticker Details ───────────────────────────────────────────────

def pull_ticker_detail(ticker: str) -> tuple | None:
    time.sleep(CALL_INTERVAL)
    resp = api_get(f"/v3/reference/tickers/{ticker}", quiet=True)
    if resp.get("status") != "OK":
        return None
    r = resp.get("results", {})
    if not r:
        return None
    return (ticker, r.get("name"), r.get("market_cap"),
            r.get("primary_exchange"), r.get("sic_code"),
            r.get("sic_description"), r.get("total_employees"),
            r.get("weighted_shares_outstanding"), r.get("list_date"),
            r.get("currency_name"), int(time.time() * 1000))


def pull_ticker_details(db, tickers: list[str]):
    cp = load_checkpoint()
    key = "ticker_details_last_idx"
    start_idx = cp.get(key, 0)
    remaining = tickers[start_idx:]
    if not remaining:
        log.info("Ticker details: already complete")
        return

    log.info(f"Ticker details: {len(remaining):,} tickers")
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(pull_ticker_detail, t): (i, t)
                   for i, t in enumerate(remaining)}
        for f in as_completed(futures):
            row = f.result()
            if row:
                db.execute(
                    """INSERT OR REPLACE INTO ticker_details
                       (ticker, name, market_cap, exchange, sic_code,
                        sic_description, employees, shares_outstanding,
                        list_date, currency, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
            done += 1
            if done % 500 == 0:
                save_checkpoint({**cp, key: start_idx + done})
                log.info(f"Ticker details [{done}/{len(remaining)}]")

    cp[key] = len(tickers)
    save_checkpoint(cp)
    log.info(f"Ticker details complete: {done:,} tickers")


# ── Stats ─────────────────────────────────────────────────────────────────

def print_stats(db):
    tables = ["daily_bars", "hourly_bars", "weekly_bars",
              "dividends", "splits", "ticker_details"]
    log.info("=" * 50)
    log.info("DATABASE STATISTICS")
    for tbl in tables:
        try:
            count = db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            log.info(f"  {tbl:<25s} {count:>12,} rows")
        except Exception:
            log.info(f"  {tbl:<25s}  (empty)")
    size_mb = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
    log.info(f"  Database size: {size_mb:.1f} MB")
    log.info("=" * 50)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    import duckdb

    log.info("=" * 50)
    log.info("MARKET DATA WAREHOUSE — Historical Pull v2")
    log.info(f"Range: {HISTORY_START} → {HISTORY_END}")
    log.info(f"Rate: {RATE_LIMIT_CALLS}/min | Workers: {MAX_WORKERS} | "
             f"Call interval: {CALL_INTERVAL:.2f}s")
    log.info("=" * 50)

    tickers = load_tickers()
    if not tickers:
        log.error("No tickers loaded — aborting")
        sys.exit(1)

    db = init_database()

    # Phase 1: Daily
    log.info("\n── Phase 1: Daily Bars ──")
    t0 = time.time()
    pull_daily_all(db, tickers)
    log.info(f"  Time: {(time.time()-t0)/60:.0f} min")

    # Phase 2: Hourly
    log.info("\n── Phase 2: Hourly Bars ──")
    t0 = time.time()
    pull_timeframe(db, tickers, "hour", "hourly_bars")
    log.info(f"  Time: {(time.time()-t0)/60:.0f} min")

    # Phase 3: Weekly
    log.info("\n── Phase 3: Weekly Bars ──")
    t0 = time.time()
    pull_timeframe(db, tickers, "week", "weekly_bars")
    log.info(f"  Time: {(time.time()-t0)/60:.0f} min")

    # Phase 4: Dividends + Splits
    log.info("\n── Phase 4: Dividends & Splits ──")
    t0 = time.time()
    pull_corporate_actions(db, tickers)
    log.info(f"  Time: {(time.time()-t0)/60:.0f} min")

    # Phase 5: Ticker Details
    log.info("\n── Phase 5: Ticker Details ──")
    t0 = time.time()
    pull_ticker_details(db, tickers)
    log.info(f"  Time: {(time.time()-t0)/60:.0f} min")

    # Done
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
    db.close()

    db = duckdb.connect(str(DB_PATH), read_only=True)
    print_stats(db)
    db.close()
    log.info("✅ Historical pull complete")


if __name__ == "__main__":
    main()
