#!/usr/bin/env python3
"""
Build ALL remaining technical indicators.
Reads tickers from DuckDB, skips already-processed ones, runs batches.
Outputs progress to stdout — cron will capture this.
"""
import subprocess, sys, time, duckdb
from pathlib import Path
import os

os.environ['PYTHONUNBUFFERED'] = '1'

DB_PATH = Path.home() / "market-data" / "market_data.duckdb"
SCRIPT = Path.home() / "market-data" / "build_indicators.py"
PYTHON = "/usr/bin/python3"
BATCH_SIZE = 100

# Count already done
db = duckdb.connect(str(DB_PATH), read_only=True)
try:
    done = set(r[0] for r in db.execute(
        "SELECT DISTINCT ticker FROM technical_indicators"
    ).fetchall())
except Exception:
    done = set()

all_tickers = sorted(set(r[0] for r in db.execute("""
    SELECT DISTINCT d.ticker FROM daily_bars d
    WHERE EXISTS (SELECT 1 FROM hourly_bars h WHERE h.ticker = d.ticker)
""").fetchall()))
db.close()

todo = [t for t in all_tickers if t not in done]
print(f"Already done: {len(done):,}  |  Remaining: {len(todo):,}  |  Total: {len(all_tickers):,}")
sys.stdout.flush()

if not todo:
    print("✅ All tickers already processed!")
    sys.exit(0)

batches = [todo[i:i+BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
t0_total = time.time()
failed = 0

for bi, batch in enumerate(batches):
    t0 = time.time()
    eta_remaining = (time.time() - t0_total) / max(bi, 1) * (len(batches) - bi) if bi > 0 else 0
    
    print(f"[{bi+1}/{len(batches)}] {len(batch)} tickers | ETA remaining: {eta_remaining/60:.0f} min")
    sys.stdout.flush()
    
    cmd = [PYTHON, str(SCRIPT), "--tickers"] + batch
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    
    elapsed = time.time() - t0
    if result.returncode == 0:
        lines = result.stdout.strip().split('\n')
        last_info = [l for l in lines[-5:] if l.strip()]
        for line in last_info:
            print(f"  {line}")
        print(f"  ⏱️  {elapsed:.0f}s ({elapsed/60:.1f} min)")
    else:
        failed += 1
        print(f"  ❌ FAILED (exit {result.returncode})")
        err = result.stderr[-300:]
        if err:
            print(f"  {err}")
        if failed >= 3:
            print("Too many failures, stopping.")
            break
    
    sys.stdout.flush()

total_elapsed = time.time() - t0_total
print(f"\nDone: {len(batches)-failed}/{len(batches)} batches in {total_elapsed/60:.1f} min")
if failed:
    print(f"⚠️  {failed} batches failed")
