#!/usr/bin/env python3
"""Bulk insert NDJSON files into DuckDB using native read_json."""
import duckdb, time
from pathlib import Path

RAW = Path.home() / "market-data" / "raw"
DB = Path.home() / "market-data" / "market_data.duckdb"
db = duckdb.connect(str(DB))

# Weekly
t0 = time.time()
print("Importing weekly...", flush=True)
db.execute(f"""
    INSERT OR IGNORE INTO weekly_bars
    SELECT _ticker AS ticker, t AS timestamp, o, h, l, c, v, vw, n
    FROM read_json_auto('{RAW}/weekly.ndjson',
        format='newline_delimited', ignore_errors=true)
""")
c = db.execute("SELECT COUNT(*) FROM weekly_bars").fetchone()[0]
print(f"  Weekly: {c:,} rows in {time.time()-t0:.0f}s", flush=True)

# Dividends
t0 = time.time()
print("Importing dividends...", flush=True)
db.execute(f"""
    INSERT OR IGNORE INTO dividends
    SELECT _ticker AS ticker, ex_dividend_date, cash_amount,
           declaration_date, pay_date, record_date,
           frequency, dividend_type, currency
    FROM read_json_auto('{RAW}/dividends.ndjson',
        format='newline_delimited', ignore_errors=true)
""")
c = db.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
print(f"  Dividends: {c:,} rows in {time.time()-t0:.0f}s", flush=True)

# Splits
t0 = time.time()
print("Importing splits...", flush=True)
db.execute(f"""
    INSERT OR IGNORE INTO splits
    SELECT _ticker AS ticker, execution_date, split_from, split_to
    FROM read_json_auto('{RAW}/splits.ndjson',
        format='newline_delimited', ignore_errors=true)
""")
c = db.execute("SELECT COUNT(*) FROM splits").fetchone()[0]
print(f"  Splits: {c:,} rows in {time.time()-t0:.0f}s", flush=True)

# Stats
db.close()
db = duckdb.connect(str(DB), read_only=True)
for tbl in ["daily_bars", "weekly_bars", "dividends", "splits"]:
    c = db.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    print(f"  {tbl}: {c:,}", flush=True)
db.close()
size_mb = DB.stat().st_size / 1024**2
print(f"  DB size: {size_mb:.1f} MB", flush=True)
print("Done", flush=True)
