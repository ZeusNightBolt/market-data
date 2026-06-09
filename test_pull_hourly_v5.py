#!/usr/bin/env python3
"""
Unit Tests for pull_hourly.py v5 — Year-Chunked Concurrency
============================================================
Tests all three v5 optimizations:
  1. get_incomplete_tickers() — detects incomplete hourly coverage vs daily baseline
  2. generate_chunks() — correct year splitting
  3. pull_hourly_chunk() — correct API response parsing
  4. End-to-end: dry-run, live pull on 3 tickers, post-pull DB verification
"""

import sys, json, os
from datetime import date
from pathlib import Path

BASE_DIR = Path.home() / "market-data"
sys.path.insert(0, str(BASE_DIR))

import duckdb
from pull_hourly import (
    generate_chunks,
    pull_hourly_chunk,
    get_incomplete_tickers,
    load_vti_tickers,
    bulk_insert_ndjson,
    RAW_DIR, DB_PATH,
)

PASS = 0
FAIL = 0

def check(test_name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {test_name}")
    else:
        FAIL += 1
        print(f"  ❌ {test_name}  — {detail}")


# ═══════════════════════════════════════════════════════════════════════
# Test 1: generate_chunks() — Year Splitting
# ═══════════════════════════════════════════════════════════════════════

def test_generate_chunks():
    print("\n─── Test 1: generate_chunks() ───")

    today = date.today()
    chunks = generate_chunks("TEST")

    check("Returns list of tuples", isinstance(chunks, list) and all(len(c) == 3 for c in chunks),
          f"got {type(chunks)}")

    expected_years = today.year - 2021 + 1  # 2021 through current year
    check(f"Correct year count ({expected_years})", len(chunks) == expected_years,
          f"got {len(chunks)} chunks: {chunks}")

    # First chunk should start 2021-06-01
    check("First chunk starts 2021-06-01",
          chunks[0][1] == "2021-06-01",
          f"got {chunks[0][1]}")

    # Last chunk should end today
    check(f"Last chunk ends today ({today.isoformat()})",
          chunks[-1][2] == today.isoformat(),
          f"got {chunks[-1][2]}")

    # Middle chunks should be full years
    mid = chunks[1] if len(chunks) > 2 else None
    if mid:
        check("Middle chunk is full year (Jan 1 – Dec 31)",
              mid[1].endswith("-01-01") and mid[2].endswith("-12-31"),
              f"got {mid[1]} → {mid[2]}")

    # No overlapping or reversed dates
    for i in range(len(chunks) - 1):
        check(f"Chunk {i} → {i+1} sequential",
              chunks[i][2] < chunks[i+1][1] or
              (chunks[i][0] == chunks[i+1][0] and chunks[i][1] < chunks[i+1][1]),
              f"{chunks[i][2]} >= {chunks[i+1][1]}")

    # All tickers match
    check("All ticker fields match", all(c[0] == "TEST" for c in chunks))


# ═══════════════════════════════════════════════════════════════════════
# Test 2: get_incomplete_tickers() — Detection Logic
# ═══════════════════════════════════════════════════════════════════════

def test_get_incomplete_tickers():
    print("\n─── Test 2: get_incomplete_tickers() ───")

    incomplete = get_incomplete_tickers()

    check("Returns a list", isinstance(incomplete, list))
    check("List is not empty (gap exists)", len(incomplete) > 0,
          "No incomplete tickers found — DB may already be complete")

    # MSFT should definitely be incomplete (1,395 hourly vs 1,247 daily)
    if "MSFT" in load_vti_tickers():
        check("MSFT is flagged as incomplete", "MSFT" in incomplete,
              f"MSFT has 1,395 hourly / 1,247 daily — should be incomplete")

    # AAPL should NOT be incomplete (19,884 hourly vs 1,247 daily)
    if "AAPL" in load_vti_tickers():
        check("AAPL is NOT flagged as incomplete", "AAPL" not in incomplete,
              f"AAPL has 19,884 hourly — should be complete")

    # Spot-check: any ticker in incomplete should have hourly < daily * 5.5
    if incomplete:
        db = duckdb.connect(str(DB_PATH))
        test_ticker = incomplete[0]
        hc = db.execute("SELECT COUNT(*) FROM hourly_bars WHERE ticker=?", [test_ticker]).fetchone()[0]
        dc = db.execute("SELECT COUNT(*) FROM daily_bars WHERE ticker=?", [test_ticker]).fetchone()[0]
        db.close()
        check(f"{test_ticker}: {hc} hourly < {dc} daily × 5.5",
              hc < dc * 5.5,
              f"{hc} >= {dc * 5.5}")

    print(f"  → Found {len(incomplete):,} incomplete tickers")


# ═══════════════════════════════════════════════════════════════════════
# Test 3: pull_hourly_chunk() — API Response Parsing
# ═══════════════════════════════════════════════════════════════════════

def test_pull_hourly_chunk():
    print("\n─── Test 3: pull_hourly_chunk() — API integration ───")

    # Test with a known-good ticker, 1-month range (fast)
    rows = pull_hourly_chunk("AAPL", "2026-05-01", "2026-05-18")

    check("Returns list", isinstance(rows, list))
    check("Returns data (AAPL May 2026)", len(rows) > 0,
          f"got {len(rows)} rows — may be weekend/holiday")

    if rows:
        # Check row structure
        r = rows[0]
        check("Row is a tuple", isinstance(r, tuple))
        check("Row has 9 elements", len(r) == 9, f"got {len(r)}")
        check("Element 0 is ticker string", r[0] == "AAPL")
        check("Element 1 is timestamp (int)", isinstance(r[1], int) and r[1] > 1_700_000_000_000)
        check("Elements 2-5 are prices (float/int)", all(isinstance(r[i], (int, float)) for i in range(2, 6)))
        check("Element 6 is volume (float/int)", isinstance(r[6], (int, float)))
        check("Element 7 is VWAP (float/int or None)", r[7] is None or isinstance(r[7], (int, float)))

        # Verify chronological ordering
        for i in range(len(rows) - 1):
            if rows[i][1] >= rows[i+1][1]:
                check("Rows are chronologically ordered", False,
                      f"Row {i} ts={rows[i][1]} >= row {i+1} ts={rows[i+1][1]}")
                break
        else:
            check("Rows are chronologically ordered", True)

        print(f"  → Got {len(rows)} hourly bars for AAPL May 1-18 2026")

    # Test empty range (future date)
    empty = pull_hourly_chunk("AAPL", "2099-01-01", "2099-01-31")
    check("Empty range returns []", empty == [], f"got {len(empty)} rows")

    # Test bad ticker
    bad = pull_hourly_chunk("ZZZZXYZZY", "2026-01-01", "2026-01-31")
    check("Bad ticker returns []", bad == [], f"got {len(bad)} rows")


# ═══════════════════════════════════════════════════════════════════════
# Test 4: End-to-end — Dry Run
# ═══════════════════════════════════════════════════════════════════════

def test_dry_run():
    print("\n─── Test 4: Dry Run ───")

    incomplete = get_incomplete_tickers()
    total_chunks = sum(len(generate_chunks(t)) for t in incomplete)

    check("Chunks > tickers (year splitting works)", total_chunks > len(incomplete),
          f"{total_chunks} chunks for {len(incomplete)} tickers")

    # Each ticker should get ~5-6 chunks (2021-2026)
    expected_per_ticker = date.today().year - 2021 + 1
    avg_chunks = total_chunks / len(incomplete) if incomplete else 0
    check(f"Avg chunks/ticker ≈ {expected_per_ticker}",
          abs(avg_chunks - expected_per_ticker) < 0.5,
          f"got {avg_chunks:.1f}")

    print(f"  → {len(incomplete):,} tickers → {total_chunks:,} year chunks")


# ═══════════════════════════════════════════════════════════════════════
# Test 5: Live Pull — 3 Tickers
# ═══════════════════════════════════════════════════════════════════════

def test_live_pull():
    print("\n─── Test 5: Live Pull — 3 Tickers ───")

    # Pick 3 tickers that are definitely incomplete (not AAPL)
    incomplete = get_incomplete_tickers()
    test_tickers = [t for t in ["MSFT", "GOOGL", "AMZN"] if t in incomplete][:3]

    if len(test_tickers) < 3:
        # Fall back to first 3 incomplete tickers
        test_tickers = incomplete[:3]

    if not test_tickers:
        print("  ⚠️  No incomplete tickers to pull — DB may be fully complete")
        return

    print(f"  Pulling: {', '.join(test_tickers)}")

    # Get pre-pull row count
    db = duckdb.connect(str(DB_PATH))
    pre_counts = {}
    for t in test_tickers:
        pre_counts[t] = db.execute("SELECT COUNT(*) FROM hourly_bars WHERE ticker=?", [t]).fetchone()[0]
    db.close()

    print(f"  Pre-pull hourly bars: { {t: pre_counts[t] for t in test_tickers} }")

    # Do the pull (year-chunked, 6 workers)
    import time
    t0 = time.time()

    # Build chunks for just these tickers
    all_chunks = []
    for t in test_tickers:
        all_chunks.extend(generate_chunks(t))

    print(f"  Chunks to pull: {len(all_chunks)}")

    # Manual mini-pull using the core function
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    results = {}
    results_lock = threading.Lock()
    succeeded = 0
    failed = 0

    def pull_one(ticker, start_date, end_date):
        nonlocal succeeded, failed
        rows = pull_hourly_chunk(ticker, start_date, end_date)
        with results_lock:
            if rows:
                results.setdefault(ticker, []).extend(rows)
                succeeded += 1
            else:
                failed += 1

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = []
        for ticker, start_date, end_date in all_chunks:
            futures.append(pool.submit(pull_one, ticker, start_date, end_date))
        for f in as_completed(futures, timeout=300):
            try:
                f.result(timeout=120)
            except Exception as e:
                failed += 1
                print(f"  ⚠️  Chunk failed: {e}")

    elapsed = time.time() - t0
    print(f"  Pull: {succeeded}/{len(all_chunks)} chunks succeeded, {failed} failed in {elapsed:.1f}s")

    # Merge results into DB
    if results:
        # Write temp NDJSON
        temp_ndjson = RAW_DIR / "hourly.ndjson"
        # Don't clobber existing — use test-specific file
        test_ndjson = RAW_DIR / "test_hourly.ndjson"
        with open(test_ndjson, "w") as f:
            for ticker, rows_list in results.items():
                for row in rows_list:
                    f.write(json.dumps({
                        "_ticker": row[0], "t": row[1],
                        "o": row[2], "h": row[3], "l": row[4], "c": row[5],
                        "v": row[6], "vw": row[7], "n": row[8]
                    }) + "\n")

        # Insert using DuckDB
        t0 = time.time()
        db = duckdb.connect(str(DB_PATH))
        db.execute("CREATE INDEX IF NOT EXISTS idx_hourly_ticker_ts ON hourly_bars(ticker, timestamp)")
        db.execute(f"""
            INSERT INTO hourly_bars
            SELECT j._ticker, j.t, j.o, j.h, j.l, j.c, j.v, j.vw, j.n
            FROM read_json('{test_ndjson}', format='newline_delimited',
                           columns={{_ticker: 'VARCHAR', t: 'BIGINT', o: 'DOUBLE',
                                     h: 'DOUBLE', l: 'DOUBLE', c: 'DOUBLE',
                                     v: 'DOUBLE', vw: 'DOUBLE', n: 'INTEGER'}}) j
            WHERE NOT EXISTS (
                SELECT 1 FROM hourly_bars h
                WHERE h.ticker = j._ticker AND h.timestamp = j.t
            )
        """)
        post_counts = {}
        for t in test_tickers:
            post_counts[t] = db.execute("SELECT COUNT(*) FROM hourly_bars WHERE ticker=?", [t]).fetchone()[0]
        db.close()
        merge_time = time.time() - t0
        print(f"  DB merge: {merge_time:.1f}s")

        # Verify
        for t in test_tickers:
            added = post_counts[t] - pre_counts[t]
            check(f"{t}: hourly bars increased ({pre_counts[t]} → {post_counts[t]}, +{added})",
                  post_counts[t] > pre_counts[t],
                  f"No new bars added for {t}")

        # Clean up test NDJSON
        test_ndjson.unlink()

    else:
        print("  ❌ No results to merge — all chunks failed")
        check("Live pull returned data", False)


# ═══════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("pull_hourly.py v5 — Unit Tests")
    print("=" * 60)

    RAW_DIR.mkdir(exist_ok=True)

    test_generate_chunks()
    test_get_incomplete_tickers()
    test_pull_hourly_chunk()
    test_dry_run()
    test_live_pull()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)

    sys.exit(0 if FAIL == 0 else 1)
