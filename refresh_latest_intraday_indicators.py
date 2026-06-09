#!/usr/bin/env python3
"""
Refresh latest 1h and 4h recursive indicators in DuckDB.

Fast intraday tail updater for `technical_indicators` timeframes '1h' and '4h'.
Computes only the latest row per ticker per timeframe from the most recent
hourly bars, using the same Wilder/EMA functions as build_indicators.py.
Resamples 1h → 4h via resample_to_4h().

This avoids recomputing full indicator history for 3,300 tickers after
every hourly_bars append in the daily cron.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_indicators import atr_wilder, ema, rsi_wilder, resample_to_4h

DB_PATH = Path.home() / "market-data" / "market_data.duckdb"
# Batch size for window-function queries that scan hourly_bars.
# 33.4M rows × ROW_NUMBER() OVER (PARTITION BY ticker) exhausts DuckDB's
# default memory_limit (~4.5 GB). Batching limits per-query scan pressure.
_TICKER_BATCH_SIZE = 500

TECHNICAL_INDICATOR_COLUMNS = [
    "ticker", "timestamp", "timeframe", "open", "high", "low", "close", "volume",
    "sma_20", "sma_50", "sma_200", "ema_20", "rsi_14", "atr_14",
    "keltner_middle", "keltner_upper", "keltner_lower", "anchored_vwap",
    "vwap_anchor", "volume_spike", "bb_upper", "bb_lower", "bb_width",
    "macd_line", "macd_signal", "macd_histogram", "keltner_upper_1_5",
    "keltner_lower_1_5", "keltner_upper_3_0", "keltner_lower_3_0",
    "vwap", "poc_approx",
]


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """,
        [table],
    ).fetchone()[0])


def _compute_rows(bars_df: pd.DataFrame, timeframe: str) -> list[dict]:
    """Compute indicator rows for the latest bar per ticker in bars_df."""
    rows = []
    for ticker, g in bars_df.groupby("ticker", sort=False):
        if len(g) < 50:
            continue
        g = g.reset_index(drop=True).sort_values("timestamp")
        last = g.iloc[-1]
        atr14 = atr_wilder(g["high"], g["low"], g["close"], 14)
        atr10 = atr_wilder(g["high"], g["low"], g["close"], 10)
        ema20 = ema(g["close"], 20)
        rsi14 = rsi_wilder(g["close"], 14)
        rows.append({
            "ticker": ticker,
            "timestamp": int(last["timestamp"]),
            "timeframe": timeframe,
            "open": float(last["open"]) if pd.notna(last["open"]) else None,
            "high": float(last["high"]) if pd.notna(last["high"]) else None,
            "low": float(last["low"]) if pd.notna(last["low"]) else None,
            "close": float(last["close"]) if pd.notna(last["close"]) else None,
            "volume": float(last["volume"]) if pd.notna(last["volume"]) else None,
            "sma_20": None,
            "sma_50": None,
            "sma_200": None,
            "ema_20": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "rsi_14": float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else None,
            "atr_14": float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else None,
            "keltner_middle": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "keltner_upper": float(ema20.iloc[-1] + 2.0 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower": float(ema20.iloc[-1] - 2.0 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "anchored_vwap": None,
            "vwap_anchor": None,
            "volume_spike": False,
            "bb_upper": None,
            "bb_lower": None,
            "bb_width": None,
            "macd_line": None,
            "macd_signal": None,
            "macd_histogram": None,
            "keltner_upper_1_5": float(ema20.iloc[-1] + 1.5 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower_1_5": float(ema20.iloc[-1] - 1.5 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_upper_3_0": float(ema20.iloc[-1] + 3.0 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower_3_0": float(ema20.iloc[-1] - 3.0 * atr10.iloc[-1])
                if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "vwap": None,
            "poc_approx": None,
        })
    return rows


def compute_1h_latest(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str] | None = None,
    lookback_rows: int = 1000,
) -> pd.DataFrame:
    """Compute latest 1h indicator rows for tickers with new hourly bars."""
    ticker_clause = f"AND h.ticker IN ({','.join(['?'] * len(tickers))})" if tickers else ""
    params: list[object] = [*tickers] if tickers else []

    if table_exists(con, "technical_indicators"):
        query = f"""
            WITH latest_bars AS (
                SELECT h.ticker, MAX(h.timestamp) AS latest_bar_ts
                FROM hourly_bars h
                WHERE 1=1 {ticker_clause}
                GROUP BY h.ticker
            ), latest_indicators AS (
                SELECT ticker, MAX(timestamp) AS latest_indicator_ts
                FROM technical_indicators
                WHERE timeframe = '1h'
                GROUP BY ticker
            )
            SELECT b.ticker
            FROM latest_bars b
            LEFT JOIN latest_indicators i USING (ticker)
            WHERE i.latest_indicator_ts IS NULL OR b.latest_bar_ts > i.latest_indicator_ts
            ORDER BY b.ticker
        """
    else:
        query = f"""
            SELECT h.ticker
            FROM hourly_bars h
            WHERE 1=1 {ticker_clause}
            GROUP BY h.ticker
            ORDER BY h.ticker
        """
    target_tickers = [r[0] for r in con.execute(query, params).fetchall()]
    if not target_tickers:
        return pd.DataFrame(columns=TECHNICAL_INDICATOR_COLUMNS)

    # Batch window-function query to avoid OOM on 33.4M row scan.
    # Each batch of _TICKER_BATCH_SIZE tickers keeps in-flight memory bounded.
    all_bars = []
    for i in range(0, len(target_tickers), _TICKER_BATCH_SIZE):
        batch_tickers = target_tickers[i:i + _TICKER_BATCH_SIZE]
        placeholders = ",".join(["?"] * len(batch_tickers))
        batch_df = con.execute(f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
                FROM hourly_bars
                WHERE ticker IN ({placeholders})
            )
            SELECT ticker, timestamp, open, high, low, close, volume
            FROM ranked
            WHERE rn <= ?
            ORDER BY ticker, timestamp
        """, batch_tickers + [lookback_rows]).fetchdf()
        if not batch_df.empty:
            all_bars.append(batch_df)
    bars = pd.concat(all_bars, ignore_index=True) if all_bars else pd.DataFrame()

    if bars.empty:
        return pd.DataFrame(columns=TECHNICAL_INDICATOR_COLUMNS)

    return pd.DataFrame(_compute_rows(bars, "1h"), columns=TECHNICAL_INDICATOR_COLUMNS)


def compute_4h_latest(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str] | None = None,
    lookback_rows: int = 2000,
) -> pd.DataFrame:
    """Compute latest 4h indicator rows by resampling 1h → 4h."""
    ticker_clause = f"AND h.ticker IN ({','.join(['?'] * len(tickers))})" if tickers else ""
    params: list[object] = [*tickers] if tickers else []

    if table_exists(con, "technical_indicators"):
        query = f"""
            WITH latest_bars AS (
                SELECT h.ticker, MAX(h.timestamp) AS latest_bar_ts
                FROM hourly_bars h
                WHERE 1=1 {ticker_clause}
                GROUP BY h.ticker
            ), latest_indicators AS (
                SELECT ticker, MAX(timestamp) AS latest_indicator_ts
                FROM technical_indicators
                WHERE timeframe = '4h'
                GROUP BY ticker
            )
            SELECT b.ticker
            FROM latest_bars b
            LEFT JOIN latest_indicators i USING (ticker)
            WHERE i.latest_indicator_ts IS NULL OR b.latest_bar_ts > i.latest_indicator_ts
            ORDER BY b.ticker
        """
    else:
        query = f"""
            SELECT h.ticker
            FROM hourly_bars h
            WHERE 1=1 {ticker_clause}
            GROUP BY h.ticker
            ORDER BY h.ticker
        """
    target_tickers = [r[0] for r in con.execute(query, params).fetchall()]
    if not target_tickers:
        return pd.DataFrame(columns=TECHNICAL_INDICATOR_COLUMNS)

    # Batch window-function query to avoid OOM on 33.4M row scan.
    # Each batch of _TICKER_BATCH_SIZE tickers keeps in-flight memory bounded.
    # Pull enough 1h bars so resampling yields ~500 4h bars per ticker
    all_bars_1h = []
    for i in range(0, len(target_tickers), _TICKER_BATCH_SIZE):
        batch_tickers = target_tickers[i:i + _TICKER_BATCH_SIZE]
        placeholders = ",".join(["?"] * len(batch_tickers))
        batch_df = con.execute(f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
                FROM hourly_bars
                WHERE ticker IN ({placeholders})
            )
            SELECT ticker, timestamp, open, high, low, close, volume
            FROM ranked
            WHERE rn <= ?
            ORDER BY ticker, timestamp
        """, batch_tickers + [lookback_rows]).fetchdf()
        if not batch_df.empty:
            all_bars_1h.append(batch_df)
    bars_1h = pd.concat(all_bars_1h, ignore_index=True) if all_bars_1h else pd.DataFrame()

    if bars_1h.empty:
        return pd.DataFrame(columns=TECHNICAL_INDICATOR_COLUMNS)

    # Resample to 4h per ticker
    rows = []
    for ticker, g in bars_1h.groupby("ticker", sort=False):
        g = g.reset_index(drop=True).sort_values("timestamp")
        try:
            df_4h = resample_to_4h(g)
        except Exception:
            continue
        if len(df_4h) < 50:
            continue
        df_4h["ticker"] = ticker
        rows.extend(_compute_rows(df_4h, "4h"))

    return pd.DataFrame(rows, columns=TECHNICAL_INDICATOR_COLUMNS)


def upsert(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    if df.empty:
        return
    con.execute("""
        CREATE TABLE IF NOT EXISTS technical_indicators (
            ticker VARCHAR, timestamp BIGINT, timeframe VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
            sma_20 DOUBLE, sma_50 DOUBLE, sma_200 DOUBLE, ema_20 DOUBLE,
            rsi_14 DOUBLE, atr_14 DOUBLE,
            keltner_middle DOUBLE, keltner_upper DOUBLE, keltner_lower DOUBLE,
            anchored_vwap DOUBLE, vwap_anchor VARCHAR, volume_spike BOOLEAN,
            bb_upper DOUBLE, bb_lower DOUBLE, bb_width DOUBLE,
            macd_line DOUBLE, macd_signal DOUBLE, macd_histogram DOUBLE,
            keltner_upper_1_5 DOUBLE, keltner_lower_1_5 DOUBLE,
            keltner_upper_3_0 DOUBLE, keltner_lower_3_0 DOUBLE,
            vwap DOUBLE, poc_approx DOUBLE,
            PRIMARY KEY (ticker, timestamp, timeframe)
        )
    """)
    con.register("__latest_intraday_indicators_df", df)
    con.execute("""
        DELETE FROM technical_indicators t
        USING __latest_intraday_indicators_df s
        WHERE t.ticker = s.ticker AND t.timestamp = s.timestamp AND t.timeframe = s.timeframe
    """)
    columns_sql = ", ".join(df.columns)
    con.execute(f"INSERT INTO technical_indicators ({columns_sql}) SELECT {columns_sql} FROM __latest_intraday_indicators_df")


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh latest 1h and 4h technical_indicators rows")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--tickers", nargs="*", help="Optional ticker subset")
    ap.add_argument("--1h-lookback", type=int, default=1000, help="1h lookback rows per ticker")
    ap.add_argument("--4h-lookback", type=int, default=2000, help="1h lookback rows for 4h resample")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-1h", action="store_true")
    ap.add_argument("--skip-4h", action="store_true")
    args = ap.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    con = duckdb.connect(args.db)
    con.execute("SET memory_limit = '8GB'")
    con.execute("SET threads = 2")
    con.execute("SET preserve_insertion_order = false")
    try:
        if not args.skip_1h:
            df_1h = compute_1h_latest(con, tickers=tickers, lookback_rows=args.__dict__["1h_lookback"])
            print(f"Latest 1h indicator rows computed: {len(df_1h):,}")
            if not df_1h.empty and not args.dry_run:
                upsert(con, df_1h)
                print("Upserted latest 1h rows into technical_indicators")
        if not args.skip_4h:
            df_4h = compute_4h_latest(con, tickers=tickers, lookback_rows=args.__dict__["4h_lookback"])
            print(f"Latest 4h indicator rows computed: {len(df_4h):,}")
            if not df_4h.empty and not args.dry_run:
                upsert(con, df_4h)
                print("Upserted latest 4h rows into technical_indicators")
    finally:
        con.close()


if __name__ == "__main__":
    main()
