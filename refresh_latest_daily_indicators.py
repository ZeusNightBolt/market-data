#!/usr/bin/env python3
"""
Refresh latest daily recursive indicators in DuckDB.

Fast daily tail updater for `technical_indicators` timeframe='daily'.
It computes only the latest daily row per ticker from the most recent daily bars,
using the same tested Wilder/EMA functions as build_indicators.py.

This avoids the old cron anti-pattern of recomputing full indicator history for
all stale tickers after every daily_bars append.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_indicators import atr_wilder, ema, rsi_wilder

DB_PATH = Path.home() / "market-data" / "market_data.duckdb"
# Batch size for window-function queries that scan daily_bars.
# Prevents OOM on first-run / catch-up when all 3,300 tickers are in the IN clause.
_TICKER_BATCH_SIZE = 500


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """,
        [table],
    ).fetchone()[0])


TECHNICAL_INDICATOR_COLUMNS = [
    "ticker", "timestamp", "timeframe", "open", "high", "low", "close", "volume",
    "sma_20", "sma_50", "sma_200", "ema_20", "rsi_14", "atr_14",
    "keltner_middle", "keltner_upper", "keltner_lower", "anchored_vwap",
    "vwap_anchor", "volume_spike", "bb_upper", "bb_lower", "bb_width",
    "macd_line", "macd_signal", "macd_histogram", "keltner_upper_1_5",
    "keltner_lower_1_5", "keltner_upper_3_0", "keltner_lower_3_0",
    "vwap", "poc_approx",
]


def compute_latest(con: duckdb.DuckDBPyConnection, tickers: list[str] | None = None, lookback_rows: int = 260) -> pd.DataFrame:
    ticker_clause = f"WHERE d.ticker IN ({','.join(['?'] * len(tickers))})" if tickers else ""
    if table_exists(con, "technical_indicators"):
        target_tickers = [r[0] for r in con.execute(f"""
            WITH latest_bars AS (
                SELECT d.ticker, MAX(d.timestamp) AS latest_bar_ts
                FROM daily_bars d
                {ticker_clause}
                GROUP BY d.ticker
            ), latest_indicators AS (
                SELECT ticker, MAX(timestamp) AS latest_indicator_ts
                FROM technical_indicators
                WHERE timeframe = 'daily'
                GROUP BY ticker
            )
            SELECT b.ticker
            FROM latest_bars b
            LEFT JOIN latest_indicators i USING (ticker)
            WHERE i.latest_indicator_ts IS NULL OR b.latest_bar_ts > i.latest_indicator_ts
            ORDER BY b.ticker
        """, tickers if tickers else []).fetchall()]
    else:
        target_tickers = [r[0] for r in con.execute(f"""
            SELECT d.ticker
            FROM daily_bars d
            {ticker_clause}
            GROUP BY d.ticker
            ORDER BY d.ticker
        """, tickers if tickers else []).fetchall()]
    if not target_tickers:
        return pd.DataFrame(columns=TECHNICAL_INDICATOR_COLUMNS)

    params: list[object] = target_tickers + [lookback_rows]

    # Incremental: first identify tickers whose latest daily bar is newer than
    # their latest daily technical row, then pull a full recent lookback window
    # for those tickers. Do NOT anti-join individual bars before ranking: when
    # only the newest bar is missing, that leaves a one-row history and RSI/ATR
    # cannot be computed.
    # Batch window-function query to avoid OOM on large ticker lists.
    # Each batch of _TICKER_BATCH_SIZE keeps in-flight memory bounded.
    all_bars = []
    for i in range(0, len(target_tickers), _TICKER_BATCH_SIZE):
        batch_tickers = target_tickers[i:i + _TICKER_BATCH_SIZE]
        batch_params = [*batch_tickers, lookback_rows]
        batch_df = con.execute(f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) AS rn
                FROM daily_bars d
                WHERE d.ticker IN ({','.join(['?'] * len(batch_tickers))})
            )
            SELECT ticker, timestamp, open, high, low, close, volume
            FROM ranked
            WHERE rn <= ?
            ORDER BY ticker, timestamp
        """, batch_params).fetchdf()
        if not batch_df.empty:
            all_bars.append(batch_df)
    bars = pd.concat(all_bars, ignore_index=True) if all_bars else pd.DataFrame()

    if bars.empty:
        return pd.DataFrame()

    # Use SQL view for SMA + volume_spike so definitions stay aligned with v_indicators_daily.
    sma_ticker_clause = f"WHERE ticker IN ({','.join(['?'] * len(target_tickers))})"
    latest_sma = con.execute(f"""
        WITH latest AS (
            SELECT ticker, MAX(timestamp) AS timestamp
            FROM daily_bars
            {sma_ticker_clause}
            GROUP BY ticker
        )
        SELECT v.ticker, v.timestamp, v.sma_20, v.sma_50, v.sma_200, v.volume_spike
        FROM v_indicators_daily v
        JOIN latest l USING (ticker, timestamp)
    """, target_tickers).fetchdf()

    sma_map = latest_sma.set_index("ticker") if not latest_sma.empty else pd.DataFrame()
    rows = []
    for ticker, g in bars.groupby("ticker", sort=False):
        if len(g) < 50:
            # Need at least 50 bars for SMA_50 to populate; fewer bars waste
            # computation on EMA/RSI/ATR with poor seed quality.
            continue
        g = g.reset_index(drop=True)
        last = g.iloc[-1]
        atr14 = atr_wilder(g["high"], g["low"], g["close"], 14)
        atr10 = atr_wilder(g["high"], g["low"], g["close"], 10)
        ema20 = ema(g["close"], 20)
        rsi14 = rsi_wilder(g["close"], 14)
        sma = sma_map.loc[ticker] if not sma_map.empty and ticker in sma_map.index else None
        rows.append({
            "ticker": ticker,
            "timestamp": int(last["timestamp"]),
            "timeframe": "daily",
            "open": float(last["open"]) if pd.notna(last["open"]) else None,
            "high": float(last["high"]) if pd.notna(last["high"]) else None,
            "low": float(last["low"]) if pd.notna(last["low"]) else None,
            "close": float(last["close"]) if pd.notna(last["close"]) else None,
            "volume": float(last["volume"]) if pd.notna(last["volume"]) else None,
            "sma_20": float(sma["sma_20"]) if sma is not None and pd.notna(sma["sma_20"]) else None,
            "sma_50": float(sma["sma_50"]) if sma is not None and pd.notna(sma["sma_50"]) else None,
            "sma_200": float(sma["sma_200"]) if sma is not None and pd.notna(sma["sma_200"]) else None,
            "ema_20": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "rsi_14": float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else None,
            "atr_14": float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else None,
            "keltner_middle": float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else None,
            "keltner_upper": float(ema20.iloc[-1] + 2.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower": float(ema20.iloc[-1] - 2.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "anchored_vwap": None,
            "vwap_anchor": None,
            "volume_spike": bool(sma["volume_spike"]) if sma is not None and pd.notna(sma["volume_spike"]) else False,
            "bb_upper": None,
            "bb_lower": None,
            "bb_width": None,
            "macd_line": None,
            "macd_signal": None,
            "macd_histogram": None,
            "keltner_upper_1_5": float(ema20.iloc[-1] + 1.5 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower_1_5": float(ema20.iloc[-1] - 1.5 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_upper_3_0": float(ema20.iloc[-1] + 3.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "keltner_lower_3_0": float(ema20.iloc[-1] - 3.0 * atr10.iloc[-1]) if pd.notna(ema20.iloc[-1]) and pd.notna(atr10.iloc[-1]) else None,
            "vwap": None,
            "poc_approx": None,
        })
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
    con.register("latest_daily_indicators_df", df)
    con.execute("""
        DELETE FROM technical_indicators t
        USING latest_daily_indicators_df s
        WHERE t.ticker = s.ticker AND t.timestamp = s.timestamp AND t.timeframe = s.timeframe
    """)
    columns_sql = ", ".join(df.columns)
    con.execute(f"INSERT INTO technical_indicators ({columns_sql}) SELECT {columns_sql} FROM latest_daily_indicators_df")


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh latest daily technical_indicators rows")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--tickers", nargs="*", help="Optional ticker subset")
    ap.add_argument("--lookback-rows", type=int, default=260)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    con = duckdb.connect(args.db)
    con.execute("SET memory_limit = '8GB'")
    con.execute("SET threads = 2")
    con.execute("SET preserve_insertion_order = false")
    try:
        df = compute_latest(con, tickers=tickers, lookback_rows=args.lookback_rows)
        print(f"Latest daily indicator rows computed: {len(df):,}")
        if not df.empty:
            max_dt = pd.to_datetime(df["timestamp"].max(), unit="ms", utc=True).date()
            print(f"Max indicator date: {max_dt}")
        if not args.dry_run:
            upsert(con, df)
            print("Upserted latest daily rows into technical_indicators")
    finally:
        con.close()


if __name__ == "__main__":
    main()
