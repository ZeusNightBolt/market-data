#!/usr/bin/env python3
"""
Create and refresh ticker_classification_cache in market_data.duckdb.

Sources, highest priority first:
1. Existing VTI enriched snapshot yfinance fields (yf_sector/yf_industry).
2. Canonical sector_map_vti.csv (S&P 500, SEC SIC->GICS, prior yfinance).
3. Polygon ticker_details SIC metadata as a low-confidence fallback.
4. yfinance serial retrieval for unresolved/stale rows.

Designed to be idempotent and resume-safe. External retrieval is serial by default
because concurrent yfinance .info calls are rate-limit prone.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

DB_DEFAULT = Path.home() / "market-data" / "market_data.duckdb"
SECTOR_MAP_DEFAULT = Path.home() / "earnings-reports" / "sector_map_vti.csv"
RAW_DIR_DEFAULT = Path.home() / "market-data" / "raw" / "classification"

DDL = """
CREATE TABLE IF NOT EXISTS ticker_classification_cache (
    ticker VARCHAR PRIMARY KEY,
    sector VARCHAR,
    industry VARCHAR,
    sub_industry VARCHAR,
    gics_sector VARCHAR,
    gics_industry_group VARCHAR,
    gics_industry VARCHAR,
    gics_sub_industry VARCHAR,
    sic_code VARCHAR,
    sic_description VARCHAR,
    country VARCHAR,
    source VARCHAR,
    source_priority INTEGER,
    confidence DOUBLE,
    raw_json VARCHAR,
    enriched_at TIMESTAMP,
    stale_after TIMESTAMP,
    error VARCHAR
);
"""

SIC_TO_SECTOR = {
    "01": "Consumer Staples", "02": "Consumer Staples", "07": "Consumer Staples", "08": "Materials", "09": "Consumer Staples",
    "10": "Materials", "12": "Energy", "13": "Energy", "14": "Materials",
    "15": "Industrials", "16": "Industrials", "17": "Industrials",
    "20": "Consumer Staples", "21": "Consumer Staples", "22": "Consumer Discretionary", "23": "Consumer Discretionary",
    "24": "Materials", "25": "Consumer Discretionary", "26": "Materials", "27": "Communication Services", "28": "Healthcare",
    "29": "Energy", "30": "Materials", "31": "Consumer Discretionary", "32": "Materials", "33": "Materials", "34": "Industrials",
    "35": "Information Technology", "36": "Information Technology", "37": "Industrials", "38": "Healthcare", "39": "Consumer Discretionary",
    "40": "Industrials", "41": "Industrials", "42": "Industrials", "44": "Industrials", "45": "Industrials", "47": "Industrials",
    "48": "Communication Services", "49": "Utilities",
    "50": "Industrials", "51": "Consumer Staples", "52": "Consumer Discretionary", "53": "Consumer Discretionary", "54": "Consumer Staples", "55": "Consumer Discretionary", "56": "Consumer Discretionary", "57": "Consumer Discretionary", "58": "Consumer Discretionary", "59": "Consumer Discretionary",
    "60": "Financials", "61": "Financials", "62": "Financials", "63": "Financials", "64": "Financials", "65": "Real Estate", "67": "Financials",
    "70": "Consumer Discretionary", "72": "Consumer Discretionary", "73": "Information Technology", "75": "Consumer Discretionary", "78": "Communication Services", "79": "Communication Services",
    "80": "Healthcare", "81": "Industrials", "82": "Consumer Discretionary", "83": "Healthcare", "87": "Industrials",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clean_str(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    s = str(x).strip()
    return s or None


def normalize_ticker(t: Any) -> str | None:
    s = clean_str(t)
    return s.upper() if s else None


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)
    con.execute("CREATE INDEX IF NOT EXISTS idx_classification_source ON ticker_classification_cache(source)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_classification_sector ON ticker_classification_cache(sector)")


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
    """, [table]).fetchone()[0] > 0


def upsert_rows(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = [
        "ticker", "sector", "industry", "sub_industry", "gics_sector", "gics_industry_group",
        "gics_industry", "gics_sub_industry", "sic_code", "sic_description", "country",
        "source", "source_priority", "confidence", "raw_json", "enriched_at", "stale_after", "error",
    ]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    # Keep highest-priority row per ticker in this batch.
    df = df.sort_values(["ticker", "source_priority", "confidence"], ascending=[True, True, False]).drop_duplicates("ticker", keep="first")
    con.register("classification_stage", df)
    col_csv = ", ".join(cols)
    con.execute("BEGIN")
    try:
        # Replace only if new row has better/equal priority, or existing row lacks both sector and industry.
        con.execute("""
            DELETE FROM ticker_classification_cache t
            USING classification_stage s
            WHERE t.ticker = s.ticker
              AND (
                t.source_priority IS NULL
                OR s.source_priority <= t.source_priority
                OR ((t.sector IS NULL OR t.sector = '') AND (t.industry IS NULL OR t.industry = ''))
              )
        """)
        con.execute(f"""
            INSERT INTO ticker_classification_cache ({col_csv})
            SELECT {col_csv}
            FROM classification_stage s
            WHERE NOT EXISTS (
                SELECT 1 FROM ticker_classification_cache t WHERE t.ticker = s.ticker
            )
        """)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.unregister("classification_stage")
    return len(df)


def bootstrap_existing(con: duckdb.DuckDBPyConnection, sector_map_path: Path) -> dict[str, int]:
    ts = now_utc()
    stale = ts + timedelta(days=120)
    counts: dict[str, int] = {}

    rows: list[dict[str, Any]] = []
    if table_exists(con, "vti_daily_enriched_latest"):
        df = con.execute("""
            SELECT ticker, yf_sector, yf_industry, yf_country, sic_code, sic_description
            FROM vti_daily_enriched_latest
            WHERE ticker IS NOT NULL
              AND ((yf_sector IS NOT NULL AND yf_sector <> '') OR (yf_industry IS NOT NULL AND yf_industry <> ''))
        """).fetchdf()
        for r in df.to_dict("records"):
            t = normalize_ticker(r.get("ticker"))
            if not t:
                continue
            rows.append({
                "ticker": t,
                "sector": clean_str(r.get("yf_sector")),
                "industry": clean_str(r.get("yf_industry")),
                "gics_sector": clean_str(r.get("yf_sector")),
                "gics_industry": clean_str(r.get("yf_industry")),
                "sic_code": clean_str(r.get("sic_code")),
                "sic_description": clean_str(r.get("sic_description")),
                "country": clean_str(r.get("yf_country")),
                "source": "warehouse_yfinance_snapshot",
                "source_priority": 10,
                "confidence": 0.85,
                "raw_json": json.dumps(r, default=str),
                "enriched_at": ts,
                "stale_after": stale,
            })
        counts["warehouse_yfinance_snapshot"] = upsert_rows(con, rows)

    rows = []
    if sector_map_path.exists():
        sm = pd.read_csv(sector_map_path)
        for r in sm.to_dict("records"):
            t = normalize_ticker(r.get("ticker"))
            sector = clean_str(r.get("sector"))
            industry = clean_str(r.get("industry"))
            if not t or not (sector or industry):
                continue
            source = clean_str(r.get("source")) or "sector_map"
            priority = 20 if source == "sp500" else 30 if source == "sec-sic" else 40
            conf = 0.98 if source == "sp500" else 0.70 if source == "sec-sic" else 0.80
            rows.append({
                "ticker": t,
                "sector": sector,
                "industry": industry,
                "gics_sector": sector,
                "gics_industry": industry,
                "source": f"sector_map:{source}",
                "source_priority": priority,
                "confidence": conf,
                "raw_json": json.dumps(r, default=str),
                "enriched_at": ts,
                "stale_after": stale,
            })
        counts["sector_map"] = upsert_rows(con, rows)

    rows = []
    if table_exists(con, "ticker_details"):
        df = con.execute("""
            SELECT ticker, sic_code, sic_description
            FROM ticker_details
            WHERE ticker IS NOT NULL
              AND (sic_code IS NOT NULL OR sic_description IS NOT NULL)
        """).fetchdf()
        for r in df.to_dict("records"):
            t = normalize_ticker(r.get("ticker"))
            sic = clean_str(r.get("sic_code"))
            sic_desc = clean_str(r.get("sic_description"))
            sector = SIC_TO_SECTOR.get(sic[:2]) if sic else None
            if not t or not (sector or sic_desc):
                continue
            rows.append({
                "ticker": t,
                "sector": sector,
                "industry": sic_desc,
                "gics_sector": sector,
                "sic_code": sic,
                "sic_description": sic_desc,
                "source": "polygon_sic_fallback",
                "source_priority": 90,
                "confidence": 0.45 if sector else 0.35,
                "raw_json": json.dumps(r, default=str),
                "enriched_at": ts,
                "stale_after": stale,
            })
        counts["polygon_sic_fallback"] = upsert_rows(con, rows)

    return counts


def tickers_to_fetch(con: duckdb.DuckDBPyConnection, stale_days: int, max_tickers: int | None) -> list[str]:
    if table_exists(con, "vti_daily_enriched_latest"):
        universe_sql = "SELECT DISTINCT ticker FROM vti_daily_enriched_latest WHERE ticker IS NOT NULL"
    elif table_exists(con, "daily_bars"):
        universe_sql = "SELECT DISTINCT ticker FROM daily_bars WHERE ticker IS NOT NULL"
    else:
        universe_sql = "SELECT DISTINCT ticker FROM ticker_details WHERE ticker IS NOT NULL"
    cutoff = now_utc() - timedelta(days=stale_days)
    q = f"""
        WITH universe AS ({universe_sql})
        SELECT u.ticker
        FROM universe u
        LEFT JOIN ticker_classification_cache c ON c.ticker = u.ticker
        WHERE c.ticker IS NULL
           OR ((c.sector IS NULL OR c.sector = '') AND (c.industry IS NULL OR c.industry = ''))
           OR (c.stale_after IS NOT NULL AND c.stale_after < ?)
           OR (c.error IS NOT NULL AND c.error <> '' AND (c.enriched_at IS NULL OR c.enriched_at < ?))
        ORDER BY u.ticker
    """
    rows = [r[0] for r in con.execute(q, [now_utc(), cutoff]).fetchall()]
    return rows[:max_tickers] if max_tickers else rows


def fetch_yfinance(ticker: str) -> dict[str, Any]:
    import yfinance as yf
    yahoo_ticker = ticker.replace("/", "-").replace(".", "-")
    obj = yf.Ticker(yahoo_ticker)
    info = obj.get_info()
    return info if isinstance(info, dict) else {}


def enrich_yfinance(con: duckdb.DuckDBPyConnection, raw_dir: Path, max_tickers: int | None, delay: float, stale_days: int) -> dict[str, Any]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    tickers = tickers_to_fetch(con, stale_days=stale_days, max_tickers=max_tickers)
    ts = now_utc()
    stale = ts + timedelta(days=120)
    out_path = raw_dir / f"yfinance_classification_{ts:%Y%m%d_%H%M%S}.ndjson"
    stats = {"attempted": 0, "success": 0, "errors": 0, "path": str(out_path), "todo_initial": len(tickers)}
    rows_batch: list[dict[str, Any]] = []

    for i, ticker in enumerate(tickers, 1):
        row: dict[str, Any] = {
            "ticker": ticker,
            "source": "yfinance_live",
            "source_priority": 15,
            "confidence": 0.88,
            "enriched_at": ts,
            "stale_after": stale,
        }
        try:
            info = fetch_yfinance(ticker)
            sector = clean_str(info.get("sector"))
            industry = clean_str(info.get("industry"))
            country = clean_str(info.get("country"))
            row.update({
                "sector": sector,
                "industry": industry,
                "gics_sector": sector,
                "gics_industry": industry,
                "country": country,
                "raw_json": json.dumps({k: info.get(k) for k in ["sector", "industry", "industryKey", "sectorKey", "country", "quoteType", "longName", "shortName", "exchange", "symbol"]}, default=str),
                "error": None if (sector or industry) else "NO_SECTOR_INDUSTRY",
            })
            if sector or industry:
                stats["success"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            row.update({"error": f"{type(e).__name__}: {e}", "confidence": 0.0, "raw_json": None})
            stats["errors"] += 1
        stats["attempted"] += 1
        with out_path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        rows_batch.append(row)
        # Merge incrementally so a crash keeps DB progress too.
        if len(rows_batch) >= 10:
            upsert_rows(con, rows_batch)
            rows_batch = []
        print(f"[{i}/{len(tickers)}] {ticker} sector={row.get('sector')} industry={row.get('industry')} error={row.get('error')}", flush=True)
        if delay > 0 and i < len(tickers):
            time.sleep(delay)
    if rows_batch:
        upsert_rows(con, rows_batch)
    return stats


def coverage(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT
          COUNT(*) AS rows,
          COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector <> '') AS with_sector,
          COUNT(*) FILTER (WHERE industry IS NOT NULL AND industry <> '') AS with_industry,
          COUNT(*) FILTER (WHERE error IS NOT NULL AND error <> '') AS with_error,
          COUNT(DISTINCT source) AS sources
        FROM ticker_classification_cache
    """).fetchdf()


def source_counts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT source, COUNT(*) AS rows,
               COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector <> '') AS with_sector,
               COUNT(*) FILTER (WHERE industry IS NOT NULL AND industry <> '') AS with_industry
        FROM ticker_classification_cache
        GROUP BY source
        ORDER BY rows DESC
    """).fetchdf()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFAULT))
    ap.add_argument("--sector-map", default=str(SECTOR_MAP_DEFAULT))
    ap.add_argument("--raw-dir", default=str(RAW_DIR_DEFAULT))
    ap.add_argument("--init", action="store_true", help="Create table/indexes")
    ap.add_argument("--bootstrap", action="store_true", help="Prefill from existing warehouse and sector_map CSV")
    ap.add_argument("--yfinance", action="store_true", help="Fetch unresolved/stale classifications from yfinance, serially")
    ap.add_argument("--max-tickers", type=int, default=None)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--stale-days", type=int, default=120)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(args.db)
    try:
        ensure_schema(con)
        if args.bootstrap or args.init:
            counts = bootstrap_existing(con, Path(args.sector_map)) if args.bootstrap else {}
            if counts:
                print("Bootstrap upserted:", counts)
        if args.yfinance:
            stats = enrich_yfinance(con, Path(args.raw_dir), args.max_tickers, args.delay, args.stale_days)
            print("YFinance stats:", stats)
        if args.status or args.bootstrap or args.yfinance or args.init:
            print("\nCoverage:")
            print(coverage(con).to_string(index=False))
            print("\nSource counts:")
            print(source_counts(con).to_string(index=False))
            if table_exists(con, "vti_daily_enriched_latest"):
                missing = con.execute("""
                    SELECT COUNT(*)
                    FROM vti_daily_enriched_latest v
                    LEFT JOIN ticker_classification_cache c ON c.ticker = v.ticker
                    WHERE c.ticker IS NULL OR ((c.sector IS NULL OR c.sector='') AND (c.industry IS NULL OR c.industry=''))
                """).fetchone()[0]
                print(f"\nVTI unresolved classifications: {missing}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
