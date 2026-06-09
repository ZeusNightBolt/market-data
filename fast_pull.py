#!/usr/bin/env python3
"""
Fast pull — save raw API responses to NDJSON, bulk insert later.
No DB writes during pull = no filesystem journal contention.
"""
import os, sys, json, time, logging
from polygon_client import PolygonClient
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = Path.home() / "market-data"
RAW_DIR = BASE_DIR / "raw"
DB_PATH = BASE_DIR / "market_data.duckdb"
LOG_PATH = BASE_DIR / "pull.log"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

HISTORY_START = "2021-06-01"
HISTORY_END = date.today().isoformat()
MAX_WORKERS = 8  # more workers since we're I/O bound, not DB-write bound

client = PolygonClient()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("fastpull")

def load_tickers():
    t = []
    with open(SECTOR_MAP) as f:
        next(f)
        for line in f:
            ticker = line.split(",")[0].strip().upper()
            if ticker: t.append(ticker)
    return t

# ── Phase 1: Pull raw JSON to disk ─────────────────────────────────────

def pull_and_save(ticker, category):
    """Pull data for one ticker and save raw JSON to NDJSON file."""
    if category == "weekly":
        resp = client._get(f"/v2/aggs/ticker/{ticker}/range/1/week/{HISTORY_START}/{HISTORY_END}",
                       {"limit": 50000, "sort": "asc"})
    elif category == "dividends":
        resp = client._get("/v3/reference/dividends", {"ticker": ticker, "limit": 1000})
    elif category == "splits":
        resp = client._get("/v3/reference/splits", {"ticker": ticker, "limit": 100})
    elif category == "details":
        resp = client._get(f"/v3/reference/tickers/{ticker}")
    else:
        return 0

    results = resp.get("results", [])
    if not results:
        return 0

    # Write one NDJSON line per result
    out_path = RAW_DIR / f"{category}.ndjson"
    lines = []
    for r in results:
        r["_ticker"] = ticker
        lines.append(json.dumps(r) + "\n")

    with open(out_path, "a") as f:
        f.writelines(lines)

    return len(results)


def pull_category_parallel(tickers, category, max_workers=MAX_WORKERS):
    """Pull all tickers for one category, saving to NDJSON."""
    out_path = RAW_DIR / f"{category}.ndjson"
    if out_path.exists():
        out_path.unlink()  # fresh start

    log.info(f"Pulling {category}: {len(tickers):,} tickers ({max_workers} workers)")
    t0 = time.time()
    total_rows = 0
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(pull_and_save, t, category): t for t in tickers}
        for f in as_completed(futures, timeout=600):
            t = futures[f]
            try:
                n = f.result()
                total_rows += n
                if n == 0:
                    errors += 1
            except Exception as e:
                errors += 1
                log.debug(f"  {t}: {e}")

            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed
                pct = done / len(tickers) * 100
                log.info(f"  [{done:,}/{len(tickers):,}] {pct:.0f}% — "
                         f"{total_rows:,} rows — {rate:.0f} tickers/sec")

    elapsed = time.time() - t0
    log.info(f"  Done: {total_rows:,} rows in {elapsed:.0f}s "
             f"({done/elapsed:.0f} tickers/sec, {errors} empty/errors)")

    # File size
    size_mb = out_path.stat().st_size / 1024**2 if out_path.exists() else 0
    log.info(f"  File: {out_path} ({size_mb:.1f} MB)")


# ── Phase 2: Bulk insert raw JSON → DuckDB ─────────────────────────────

def bulk_insert_weekly(db):
    """Read weekly.ndjson and insert into weekly_bars."""
    path = RAW_DIR / "weekly.ndjson"
    if not path.exists():
        log.info("No weekly.ndjson — skipping")
        return

    log.info(f"Bulk inserting weekly from {path.stat().st_size/1024**2:.0f}MB file...")
    t0 = time.time()
    rows = []
    batch_size = 100000

    with open(path) as f:
        for line in f:
            bar = json.loads(line)
            rows.append((
                bar["_ticker"], bar["t"], bar.get("o"), bar.get("h"),
                bar.get("l"), bar.get("c"), bar.get("v"),
                bar.get("vw"), bar.get("n")
            ))
            if len(rows) >= batch_size:
                db.executemany(
                    "INSERT OR IGNORE INTO weekly_bars "
                    "(ticker, timestamp, open, high, low, close, volume, vwap, transactions) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                rows = []

    if rows:
        db.executemany(
            "INSERT OR IGNORE INTO weekly_bars "
            "(ticker, timestamp, open, high, low, close, volume, vwap, transactions) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    count = db.execute("SELECT COUNT(*) FROM weekly_bars").fetchone()[0]
    log.info(f"  Weekly: {count:,} rows in {time.time()-t0:.0f}s")


def bulk_insert_generic(db, category, table, columns, row_fn):
    """Generic bulk insert from NDJSON to table."""
    path = RAW_DIR / f"{category}.ndjson"
    if not path.exists():
        log.info(f"No {category}.ndjson — skipping")
        return

    log.info(f"Bulk inserting {category}...")
    t0 = time.time()
    rows = []
    batch_size = 50000

    with open(path) as f:
        for line in f:
            item = json.loads(line)
            row = row_fn(item)
            if row:
                rows.append(row)
            if len(rows) >= batch_size:
                placeholders = ",".join(["?"] * len(rows[0]))
                db.executemany(
                    f"INSERT OR IGNORE INTO {table} {columns} VALUES ({placeholders})",
                    rows)
                rows = []

    if rows:
        placeholders = ",".join(["?"] * len(rows[0]))
        db.executemany(
            f"INSERT OR IGNORE INTO {table} {columns} VALUES ({placeholders})",
            rows)

    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    log.info(f"  {category}: {count:,} rows in {time.time()-t0:.0f}s")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    import duckdb

    RAW_DIR.mkdir(exist_ok=True)
    tickers = load_tickers()
    log.info(f"Loaded {len(tickers):,} tickers")

    # ── Phase 1: Pull all data to NDJSON files ──
    log.info("\n" + "=" * 50)
    log.info("PHASE 1: Pulling raw JSON to disk (no DB writes)")
    log.info("=" * 50)

    categories = [
        ("weekly", "Weekly bars"),
        ("dividends", "Dividends"),
        ("splits", "Splits"),
        ("details", "Ticker details"),
    ]

    for cat, label in categories:
        log.info(f"\n── {label} ──")
        pull_category_parallel(tickers, cat)

    log.info(f"\n✓ Phase 1 complete — raw data in {RAW_DIR}/")

    # ── Phase 2: Bulk insert into DuckDB ──
    log.info("\n" + "=" * 50)
    log.info("PHASE 2: Bulk inserting into DuckDB")
    log.info("=" * 50)

    db = duckdb.connect(str(DB_PATH))

    bulk_insert_weekly(db)
    bulk_insert_generic(db, "dividends", "dividends",
        "(ticker, ex_dividend_date, cash_amount, declaration_date, "
        "pay_date, record_date, frequency, dividend_type, currency)",
        lambda d: (d["_ticker"], d.get("ex_dividend_date"), d.get("cash_amount"),
                   d.get("declaration_date"), d.get("pay_date"),
                   d.get("record_date"), d.get("frequency"),
                   d.get("dividend_type"), d.get("currency")))

    bulk_insert_generic(db, "splits", "splits",
        "(ticker, execution_date, split_from, split_to)",
        lambda s: (s["_ticker"], s.get("execution_date"),
                   s.get("split_from"), s.get("split_to")))

    bulk_insert_generic(db, "details", "ticker_details",
        "(ticker, name, market_cap, exchange, sic_code, "
        "sic_description, employees, shares_outstanding, "
        "list_date, currency, last_updated)",
        lambda d: (d["_ticker"], d.get("name"), d.get("market_cap"),
                   d.get("primary_exchange"), d.get("sic_code"),
                   d.get("sic_description"), d.get("total_employees"),
                   d.get("weighted_shares_outstanding"), d.get("list_date"),
                   d.get("currency_name"), int(time.time() * 1000)))

    # Stats
    db.close()
    db = duckdb.connect(str(DB_PATH), read_only=True)
    log.info("\n" + "=" * 50)
    for tbl in ["daily_bars", "weekly_bars", "dividends", "splits", "ticker_details"]:
        c = db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        log.info(f"  {tbl:<25s} {c:>12,} rows")
    size = DB_PATH.stat().st_size / 1024**2
    log.info(f"  DB size: {size:.1f} MB")
    log.info("=" * 50)
    db.close()

    log.info("\n✅ Full pull + bulk insert complete")
    print("✅ Complete")


if __name__ == "__main__":
    main()
