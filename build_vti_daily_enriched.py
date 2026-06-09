#!/usr/bin/env python3
"""
Build daily enriched VTI holdings snapshot tables in DuckDB.

Creates/updates:
  - vti_daily_enriched_latest   one row per current VTI ticker for latest run
  - vti_daily_enriched_history  one row per (as_of_date, ticker), replacing same-day snapshot
  - v_vti_ticker_rich           latest-query view for single ticker lookups

Sources:
  - DuckDB daily_bars, ticker_details, v_indicators_daily, technical_indicators
  - VTI SEC holdings JSON from etf_universe_refresh.py
  - yfinance enriched universe JSON
  - DoltHub calendar enrichment JSON
  - Polygon enrichment JSON for fields not in warehouse yet: keywords, sentiment, short interest, etc.

This script does not call external APIs. It composes already-refreshed artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_indicators import atr_wilder, ema, rsi_wilder

HOME = Path.home()
DEFAULT_DB = HOME / "market-data" / "market_data.duckdb"
DEFAULT_VTI = HOME / "earnings-reports" / "universe" / "vti_tickers.json"
DEFAULT_YF = HOME / "earnings-reports" / "universe" / "enriched_universe.json"
DEFAULT_CAL = HOME / "earnings-reports" / "calendar" / "calendar_enriched.json"
DEFAULT_POLY = HOME / "earnings-reports" / "polygon" / "enriched.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def records_from_json(path: Path, key: str) -> list[dict[str, Any]]:
    d = load_json(path)
    rows = d.get(key, []) if isinstance(d, dict) else []
    return rows if isinstance(rows, list) else []


def to_float(x):
    try:
        if x in ("", None):
            return None
        return float(x)
    except Exception:
        return None


def to_int(x):
    try:
        if x in ("", None):
            return None
        return int(float(x))
    except Exception:
        return None


def latest_daily_calculated_indicators(con: duckdb.DuckDBPyConnection, lookback_rows: int = 260) -> pd.DataFrame:
    """Compute current daily RSI/ATR/Keltner from recent daily bars.

    This is a snapshot-level calculation for the latest row per ticker. It uses
    the tested Wilder functions from build_indicators.py but avoids refreshing
    the large append-only technical_indicators table just to get one current row.
    """
    bars = con.execute("""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
            FROM daily_bars
        )
        SELECT ticker, timestamp, open, high, low, close, volume
        FROM ranked
        WHERE rn <= ?
        ORDER BY ticker, timestamp
    """, [lookback_rows]).fetchdf()
    out = []
    for ticker, g in bars.groupby("ticker", sort=False):
        if len(g) < 20:
            continue
        g = g.reset_index(drop=True)
        rsi = rsi_wilder(g["close"], 14)
        atr14 = atr_wilder(g["high"], g["low"], g["close"], 14)
        atr10 = atr_wilder(g["high"], g["low"], g["close"], 10)
        ema20 = ema(g["close"], 20)
        last = g.iloc[-1]
        out.append({
            "ticker": ticker,
            "calc_indicator_date": pd.to_datetime(last["timestamp"], unit="ms", utc=True).date(),
            "calc_ema_20": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "calc_rsi_14": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,
            "calc_atr_14": float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else None,
            "calc_atr_pct": float(atr14.iloc[-1] / last["close"] * 100.0) if pd.notna(atr14.iloc[-1]) and last["close"] else None,
            "calc_keltner_middle": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "calc_keltner_upper": float(ema20.iloc[-1] + 2.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "calc_keltner_lower": float(ema20.iloc[-1] - 2.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
        })
    return pd.DataFrame(out)


def build_frame(db_path: Path, vti_path: Path, yfinance_path: Path, calendar_path: Path, polygon_path: Path, as_of: str) -> pd.DataFrame:
    # SEC holdings / universe
    vti_rows = []
    for r in records_from_json(vti_path, "tickers"):
        t = str(r.get("ticker", "")).upper().strip()
        if not t:
            continue
        vti_rows.append({
            "ticker": t,
            "cusip": r.get("cusip"),
            "holding_name": r.get("name"),
            "holding_value_usd": to_float(r.get("valUSD")),
            "holding_pct_value": to_float(r.get("pctVal")),
        })
    vti = pd.DataFrame(vti_rows).drop_duplicates("ticker")
    if vti.empty:
        raise SystemExit(f"No VTI rows loaded from {vti_path}")

    # yfinance all-universe enrichment
    yf_rows = []
    for r in records_from_json(yfinance_path, "tickers"):
        t = str(r.get("ticker", "")).upper().strip()
        if not t:
            continue
        yf_rows.append({
            "ticker": t,
            "yf_sector": r.get("sector"),
            "yf_industry": r.get("industry"),
            "yf_country": r.get("country"),
            "yf_forward_pe": to_float(r.get("forward_pe")),
            "yf_trailing_pe": to_float(r.get("trailing_pe")),
            "yf_peg_ratio": to_float(r.get("peg_ratio")),
            "yf_beta": to_float(r.get("beta")),
            "yf_book_value": to_float(r.get("book_value")),
            "yf_price_to_book": to_float(r.get("price_to_book")),
            "yf_dividend_yield": to_float(r.get("dividend_yield")),
            "yf_earnings_growth": to_float(r.get("earnings_growth")),
        })
    yf = pd.DataFrame(yf_rows).drop_duplicates("ticker") if yf_rows else pd.DataFrame({"ticker": []})

    # DoltHub earnings/calendar enrichment; one nearest upcoming row per ticker.
    cal_rows = []
    for r in records_from_json(calendar_path, "reporters"):
        t = str(r.get("ticker", "")).upper().strip()
        if not t:
            continue
        cal_rows.append({
            "ticker": t,
            "next_earnings_date": r.get("earnings_date"),
            "earnings_when": r.get("earnings_when"),
            "earnings_is_t1": bool(r.get("is_t1", False)),
            "rank": r.get("rank"),
            "value_grade": r.get("value_grade"),
            "growth_grade": r.get("growth_grade"),
            "momentum_grade": r.get("momentum_grade"),
            "vgm_grade": r.get("vgm_grade"),
            "eps_consensus": to_float(r.get("eps_consensus")),
            "eps_analyst_count": to_int(r.get("eps_analyst_count")),
            "eps_high": to_float(r.get("eps_high")),
            "eps_low": to_float(r.get("eps_low")),
            "eps_year_ago": to_float(r.get("eps_year_ago")),
            "eps_yoy_growth": to_float(r.get("eps_yoy_growth")),
            "revenue_consensus": to_float(r.get("revenue_consensus")),
            "rev_analyst_count": to_int(r.get("rev_analyst_count")),
            "rev_year_ago": to_float(r.get("rev_year_ago")),
            "rev_yoy_growth": to_float(r.get("rev_yoy_growth")),
            "eps_beat_count": to_int(r.get("eps_beat_count")),
            "eps_miss_count": to_int(r.get("eps_miss_count")),
            "eps_periods": to_int(r.get("eps_periods")),
            "eps_beat_rate": to_float(r.get("eps_beat_rate")),
            "eps_last_surprise": to_float(r.get("eps_last_surprise")),
        })
    cal = pd.DataFrame(cal_rows)
    if not cal.empty:
        cal = cal.sort_values(["ticker", "next_earnings_date"]).drop_duplicates("ticker", keep="first")
    else:
        cal = pd.DataFrame({"ticker": []})
    # Ensure integer columns don't become DOUBLE from NaN upcast
    cal_int_cols = ['eps_analyst_count', 'rev_analyst_count', 'eps_beat_count', 'eps_miss_count', 'eps_periods']
    for c in cal_int_cols:
        if c in cal.columns:
            cal[c] = cal[c].astype('Int64')

    # Fallback Polygon artifact from the earnings pipeline. Prefer the warehouse-native
    # polygon_ticker_enrichment_latest table below when it exists; this artifact only
    # covers the current earnings subset.
    poly_artifact_rows = []
    for r in records_from_json(polygon_path, "results"):
        t = str(r.get("ticker", "")).upper().strip()
        if not t:
            continue
        keywords = r.get("keywords") or []
        poly_artifact_rows.append({
            "ticker": t,
            "polygon_keywords_json": json.dumps(keywords, ensure_ascii=False),
            "polygon_keyword_count": len(keywords) if isinstance(keywords, list) else None,
            "sentiment_positive": to_int(r.get("sentiment_positive")),
            "sentiment_negative": to_int(r.get("sentiment_negative")),
            "sentiment_neutral": to_int(r.get("sentiment_neutral")),
            "sentiment_articles": to_int(r.get("sentiment_articles")),
            "sentiment_score": to_float(r.get("sentiment_score")),
            "short_interest": to_float(r.get("short_interest")),
            "days_to_cover": to_float(r.get("days_to_cover")),
            "short_avg_daily_volume": to_float(r.get("short_avg_daily_volume")),
            "short_pct_float": to_float(r.get("short_pct_float")),
            "atr_pct_polygon": to_float(r.get("atr_pct")),
            "volatility_annual_polygon": to_float(r.get("volatility_annual")),
            "from_52w_high_pct": to_float(r.get("from_52w_high_pct")),
            "from_52w_low_pct": to_float(r.get("from_52w_low_pct")),
            "avg_volume_20d_polygon": to_float(r.get("avg_volume_20d")),
            "dollar_volume_20d_polygon": to_float(r.get("dollar_volume_20d")),
        })
    poly_artifact = pd.DataFrame(poly_artifact_rows).drop_duplicates("ticker") if poly_artifact_rows else pd.DataFrame({"ticker": []})
    # Ensure integer columns don't become DOUBLE from NaN upcast
    poly_int_cols = ['polygon_keyword_count', 'sentiment_positive', 'sentiment_negative', 'sentiment_neutral', 'sentiment_articles']
    for c in poly_int_cols:
        if c in poly_artifact.columns:
            poly_artifact[c] = poly_artifact[c].astype('Int64')

    con = duckdb.connect(str(db_path), read_only=True)

    # Guard: verify all required upstream tables exist before running queries
    required_tables = ['daily_bars', 'ticker_details', 'v_indicators_daily', 'technical_indicators']
    for tbl in required_tables:
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main' AND table_name=?",
            [tbl]
        ).fetchone()[0] > 0
        if not exists:
            con.close()
            raise SystemExit(f"Required upstream table '{tbl}' does not exist in {db_path}")

    latest = con.execute("""
        WITH x AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) rn
            FROM daily_bars
        )
        SELECT ticker,
               CAST(to_timestamp(timestamp/1000) AS DATE) AS price_date,
               timestamp AS price_timestamp,
               open, high, low, close, volume, vwap, transactions
        FROM x WHERE rn=1
    """).fetchdf()
    details = con.execute("SELECT * FROM ticker_details").fetchdf()
    sql_ind = con.execute("""
        WITH x AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) rn
            FROM v_indicators_daily
        )
        SELECT ticker,
               CAST(to_timestamp(timestamp/1000) AS DATE) AS sql_indicator_date,
               sma_20, sma_50, sma_200, avg_vol_20, volume_spike AS sql_volume_spike
        FROM x WHERE rn=1
    """).fetchdf()
    rec_ind = con.execute("""
        WITH x AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) rn
            FROM technical_indicators
            WHERE timeframe='daily'
        )
        SELECT ticker,
               CAST(to_timestamp(timestamp/1000) AS DATE) AS recursive_indicator_date,
               ema_20, rsi_14, atr_14, keltner_middle, keltner_upper, keltner_lower,
               anchored_vwap, CAST(vwap_anchor AS VARCHAR) AS vwap_anchor, volume_spike AS recursive_volume_spike
        FROM x WHERE rn=1
    """).fetchdf()
    calc_ind = latest_daily_calculated_indicators(con)
    poly_table_exists = con.execute("""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name='polygon_ticker_enrichment_latest'
    """).fetchone()[0] > 0
    if poly_table_exists:
        poly_db = con.execute("""
            SELECT
                ticker,
                keywords_json AS polygon_keywords_json,
                CAST(keyword_count AS BIGINT) AS polygon_keyword_count,
                CAST(sentiment_positive AS BIGINT) AS sentiment_positive,
                CAST(sentiment_negative AS BIGINT) AS sentiment_negative,
                CAST(sentiment_neutral AS BIGINT) AS sentiment_neutral,
                CAST(sentiment_articles AS BIGINT) AS sentiment_articles,
                sentiment_score,
                short_interest,
                days_to_cover,
                short_avg_daily_volume,
                short_pct_float,
                NULL::DOUBLE AS atr_pct_polygon,
                NULL::DOUBLE AS volatility_annual_polygon,
                NULL::DOUBLE AS from_52w_high_pct,
                NULL::DOUBLE AS from_52w_low_pct,
                NULL::DOUBLE AS avg_volume_20d_polygon,
                NULL::DOUBLE AS dollar_volume_20d_polygon
            FROM polygon_ticker_enrichment_latest
        """).fetchdf()
        # Prefer full-universe warehouse rows, but keep the earnings artifact as
        # a temporary fallback for tickers not yet refreshed into DuckDB.
        poly = pd.concat([poly_db, poly_artifact], ignore_index=True).drop_duplicates("ticker", keep="first")
    else:
        poly = poly_artifact
    factor_table_exists = con.execute("""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name='ticker_keyword_factor_membership'
    """).fetchone()[0] > 0
    if factor_table_exists:
        factors = con.execute("""
            WITH latest AS (SELECT MAX(as_of_date) AS as_of_date FROM ticker_keyword_factor_membership),
            ranked AS (
                SELECT m.*, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY score DESC, matched_keyword_count DESC, basket_name) AS rn
                FROM ticker_keyword_factor_membership m
                JOIN latest l USING (as_of_date)
            )
            SELECT ticker,
                   string_agg(basket_name, ', ' ORDER BY rn) FILTER (WHERE rn <= 5) AS keyword_factor_baskets,
                   MAX(CASE WHEN rn=1 THEN basket_name END) AS primary_keyword_factor,
                   MAX(CASE WHEN rn=1 THEN score END) AS primary_keyword_factor_score,
                   MAX(CASE WHEN rn=1 THEN matched_keywords_json END) AS primary_keyword_factor_keywords
            FROM ranked
            WHERE rn <= 5
            GROUP BY ticker
        """).fetchdf()
    else:
        factors = pd.DataFrame({"ticker": []})
    con.close()

    df = vti.merge(latest, on="ticker", how="left")
    for other in [details, sql_ind, rec_ind, calc_ind, yf, cal, poly, factors]:
        if "ticker" in other.columns:
            df = df.merge(other, on="ticker", how="left")

    df.insert(0, "as_of_date", pd.to_datetime(as_of).date())
    # Useful calculated fields.
    df["market_cap_rank"] = df["market_cap"].rank(ascending=False, method="min") if "market_cap" in df else None
    df["dollar_volume"] = df["close"] * df["volume"]
    df["price_vs_sma20_pct"] = (df["close"] / df["sma_20"] - 1.0) * 100.0
    df["price_vs_sma50_pct"] = (df["close"] / df["sma_50"] - 1.0) * 100.0
    df["price_vs_sma200_pct"] = (df["close"] / df["sma_200"] - 1.0) * 100.0
    df["volume_vs_20d"] = df["volume"] / df["avg_vol_20"]
    df["has_upcoming_earnings"] = df["next_earnings_date"].notna() if "next_earnings_date" in df else False
    df["has_polygon_keywords"] = df["polygon_keyword_count"].fillna(0).astype(int) > 0 if "polygon_keyword_count" in df else False
    return df


def write_tables(db_path: Path, df: pd.DataFrame, as_of: str) -> None:
    con = duckdb.connect(str(db_path))

    # P2: Ensure index exists for efficient ticker+timestamp lookups
    con.execute("CREATE INDEX IF NOT EXISTS idx_daily_bars_ticker_ts ON daily_bars(ticker, timestamp)")

    con.register("enriched_df", df)

    # Build SELECT clause with explicit CASTs to prevent type drift from pandas NaN-upcast.
    # P0: vwap_anchor is VARCHAR in schema but NaN-upcast makes DuckDB infer INTEGER.
    # P1: integer fields (keyword_count, sentiment_*, eps_*, rev_*, etc.) become DOUBLE
    #      when pandas puts NaN in integer columns.
    cast_varchar_cols = {'vwap_anchor'}
    cast_bigint_cols = {
        'polygon_keyword_count', 'sentiment_positive', 'sentiment_negative',
        'sentiment_neutral', 'sentiment_articles',
        'eps_analyst_count', 'rev_analyst_count', 'eps_beat_count',
        'eps_miss_count', 'eps_periods',
    }
    sel_parts = []
    for c in df.columns:
        if c in cast_varchar_cols:
            sel_parts.append(f'CAST("{c}" AS VARCHAR) AS "{c}"')
        elif c in cast_bigint_cols:
            sel_parts.append(f'CAST("{c}" AS BIGINT) AS "{c}"')
        else:
            sel_parts.append(f'"{c}"')
    sel_sql = ", ".join(sel_parts)

    con.execute(f"CREATE OR REPLACE TABLE vti_daily_enriched_latest AS SELECT {sel_sql} FROM enriched_df")

    # Schema-evolve history safely. The latest table is the source schema; history
    # persists prior daily snapshots and gains new columns as the snapshot expands.
    con.execute("CREATE TABLE IF NOT EXISTS vti_daily_enriched_history AS SELECT * FROM vti_daily_enriched_latest LIMIT 0")
    latest_cols = con.execute("PRAGMA table_info('vti_daily_enriched_latest')").fetchdf()
    history_cols = con.execute("PRAGMA table_info('vti_daily_enriched_history')").fetchdf()
    have = set(history_cols["name"].tolist()) if not history_cols.empty else set()
    for _, row in latest_cols.iterrows():
        col = row["name"]
        typ = row["type"]
        if col not in have:
            con.execute(f'ALTER TABLE vti_daily_enriched_history ADD COLUMN "{col}" {typ}')
    ordered_cols = [f'"{c}"' for c in latest_cols["name"].tolist()]
    col_sql = ", ".join(ordered_cols)
    con.execute("DELETE FROM vti_daily_enriched_history WHERE as_of_date = ?", [as_of])
    con.execute(f"INSERT INTO vti_daily_enriched_history ({col_sql}) SELECT {sel_sql} FROM enriched_df")

    con.execute("""
        CREATE OR REPLACE VIEW v_vti_ticker_rich AS
        SELECT * FROM vti_daily_enriched_latest
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS vti_daily_enriched_runs (
            as_of_date DATE PRIMARY KEY,
            built_at TIMESTAMP,
            row_count BIGINT,
            with_price BIGINT,
            with_ticker_details BIGINT,
            with_yfinance_sector BIGINT,
            with_upcoming_earnings BIGINT,
            with_polygon_keywords BIGINT,
            with_calc_indicators BIGINT,
            max_price_date DATE,
            max_recursive_indicator_date DATE,
            max_calc_indicator_date DATE
        )
    """)
    run_cols = set(con.execute("PRAGMA table_info('vti_daily_enriched_runs')").fetchdf()["name"].tolist())
    if "with_calc_indicators" not in run_cols:
        con.execute("ALTER TABLE vti_daily_enriched_runs ADD COLUMN with_calc_indicators BIGINT")
    if "max_calc_indicator_date" not in run_cols:
        con.execute("ALTER TABLE vti_daily_enriched_runs ADD COLUMN max_calc_indicator_date DATE")
    con.execute("DELETE FROM vti_daily_enriched_runs WHERE as_of_date = ?", [as_of])
    con.execute("""
        INSERT INTO vti_daily_enriched_runs (
            as_of_date, built_at, row_count, with_price, with_ticker_details,
            with_yfinance_sector, with_upcoming_earnings, with_polygon_keywords,
            with_calc_indicators, max_price_date, max_recursive_indicator_date,
            max_calc_indicator_date
        )
        SELECT
            ?::DATE,
            now(),
            COUNT(*),
            SUM(CASE WHEN close IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN name IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN yf_sector IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN has_upcoming_earnings THEN 1 ELSE 0 END),
            SUM(CASE WHEN has_polygon_keywords THEN 1 ELSE 0 END),
            SUM(CASE WHEN calc_rsi_14 IS NOT NULL THEN 1 ELSE 0 END),
            MAX(price_date),
            MAX(recursive_indicator_date),
            MAX(calc_indicator_date)
        FROM enriched_df
    """, [as_of])
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build daily enriched VTI holdings tables in DuckDB")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--vti", default=str(DEFAULT_VTI))
    ap.add_argument("--yfinance", default=str(DEFAULT_YF))
    ap.add_argument("--calendar", default=str(DEFAULT_CAL))
    ap.add_argument("--polygon", default=str(DEFAULT_POLY))
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = build_frame(Path(args.db), Path(args.vti), Path(args.yfinance), Path(args.calendar), Path(args.polygon), args.as_of)
    print(f"Rows: {len(df):,}")
    print(f"With price: {df['close'].notna().sum():,}")
    print(f"With ticker details: {df['name'].notna().sum():,}")
    print(f"With yfinance sector: {df['yf_sector'].notna().sum():,}")
    print(f"With upcoming earnings: {df['has_upcoming_earnings'].sum():,}")
    print(f"With polygon keywords: {df['has_polygon_keywords'].sum():,}")
    print(f"Max price date: {df['price_date'].max()}")
    print(f"Max recursive indicator date: {df['recursive_indicator_date'].max()}")
    if args.dry_run:
        print("DRY RUN — not writing tables")
        return
    write_tables(Path(args.db), df, args.as_of)
    print("Wrote: vti_daily_enriched_latest, vti_daily_enriched_history, v_vti_ticker_rich, vti_daily_enriched_runs")


if __name__ == "__main__":
    main()
