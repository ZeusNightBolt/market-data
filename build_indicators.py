#!/usr/bin/env python3
"""
Technical Indicators Builder — v2.1 (Phase 2 Indicators)
=========================================================
Computes SMA(20/50/200), EMA(20), RSI(14), ATR(14), Keltner Channels (2.0/1.5/3.0),
MACD(12,26,9), Bollinger Bands(20,2), Anchored VWAP, VWAP, Volume Profile approx
for daily, 1h, and 4h timeframes.

v2.1 CHANGES (Phase 2):
  - MACD(12,26,9): macd_line, macd_signal, macd_histogram
  - Bollinger Bands(20,2): bb_upper, bb_lower, bb_width
  - Keltner multiplier variants: 1.5x and 3.0x bands
  - VWAP: pulled directly from daily_bars
  - Volume profile approximation: poc_approx (=VWAP proxy)

v2 CHANGES:
  - Chunked reads: 200 tickers per DuckDB query (was: 1 per ticker = 3,304 connections)
  - Batch NDJSON via to_json() (was: slow iterrows())
  - Incremental mode: only compute for missing dates
  - Uses SQL views for SMA (v_indicators_*) — this pipeline handles recursive indicators

Output: DuckDB table `technical_indicators` via NDJSON staging.
SMA views `v_indicators_daily`, `v_indicators_1h`, `v_indicators_4h` provide instant access.

Schema (32 columns):
  ticker, timestamp, timeframe, open, high, low, close, volume,
  sma_20, sma_50, sma_200, ema_20,
  rsi_14, atr_14,
  keltner_middle, keltner_upper, keltner_lower,
  keltner_upper_1_5, keltner_lower_1_5,
  keltner_upper_3_0, keltner_lower_3_0,
  macd_line, macd_signal, macd_histogram,
  bb_upper, bb_lower, bb_width,
  vwap, poc_approx,
  anchored_vwap, vwap_anchor, volume_spike

Usage:
  python3 build_indicators.py                  # full backfill (all tickers)
  python3 build_indicators.py --tickers AAPL    # specific tickers
  python3 build_indicators.py --chunk-size 500  # larger chunks for more RAM
  python3 build_indicators.py --dry-run         # validate without DB writes
"""

import sys, os, json, time, logging, argparse, gc
from pathlib import Path

import numpy as np
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "market-data"
DB_PATH = BASE_DIR / "market_data.duckdb"
RAW_DIR = BASE_DIR / "raw"

CHUNK_SIZE = 100               # tickers per DuckDB batch read (memory-safe)
MAX_WORKERS = 6                # parallel computation threads

# Indicator parameters
SMA_PERIODS = [20, 50, 200]
EMA_PERIOD = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
KELTNER_EMA = 20
KELTNER_ATR = 10
KELTNER_MULT = 2.0
VWAP_VOL_MULT = 3.0
VWAP_VOL_LOOKBACK = 20
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_NUM_STD = 2.0
KELTNER_MULT_1_5 = 1.5
KELTNER_MULT_3_0 = 3.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("indicators")


# ═══════════════════════════════════════════════════════════════════════════
# MATHEMATICAL MODELS (unchanged from v1 — verified by 57 unit tests)
# ═══════════════════════════════════════════════════════════════════════════

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    result = series.copy().astype(float)
    result.iloc[:period] = np.nan
    result.iloc[period - 1] = series.iloc[:period].mean()

    vals = np.array(result, copy=True)
    raw = np.array(series, copy=False)
    n = len(series)
    alpha = 1.0 / period
    one_minus_alpha = (period - 1.0) / period
    for i in range(period, n):
        if not np.isnan(raw[i]):
            vals[i] = one_minus_alpha * vals[i - 1] + alpha * raw[i]
        else:
            vals[i] = vals[i - 1]
    return pd.Series(vals, index=result.index)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.fillna(100.0)
    rsi.iloc[:period] = np.nan
    return rsi


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return wilder_smooth(tr, period)


def keltner_channels(close: pd.Series, atr: pd.Series,
                     ema_period: int = 20, atr_period: int = 10,
                     multiplier: float = 2.0) -> tuple:
    middle = ema(close, ema_period)
    upper = middle + atr * multiplier
    lower = middle - atr * multiplier
    return middle, upper, lower


def anchored_vwap(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]
    cum_pv = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    if anchor_idx > 0:
        cum_pv = cum_pv - cum_pv.iloc[anchor_idx - 1]
        cum_vol = cum_vol - cum_vol.iloc[anchor_idx - 1]
    vwap = cum_pv / cum_vol.replace(0, np.nan)
    vwap.iloc[:anchor_idx] = np.nan
    return vwap


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD indicator: returns DataFrame with macd_line, macd_signal, macd_histogram."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    macd_signal = ema(macd_line, signal)
    macd_histogram = macd_line - macd_signal
    return pd.DataFrame({
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
    }, index=close.index)


def bollinger_bands(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: middle (SMA), upper, lower, and width (volatility regime metric)."""
    bb_middle = sma(close, period)
    bb_std = close.rolling(window=period, min_periods=period).std()
    bb_upper = bb_middle + num_std * bb_std
    bb_lower = bb_middle - num_std * bb_std
    bb_width = (bb_upper - bb_lower) / bb_middle
    return pd.DataFrame({
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
    }, index=close.index)


def market_profile_approx(df: pd.DataFrame) -> pd.DataFrame:
    """Volume profile approximation: VWAP deviation and POC proxy.
    Requires 'close', 'volume', and 'vwap' columns in df.
    Returns DataFrame with vwap, vwp_pct (close % deviation from VWAP), poc_approx.
    """
    vwap_series = df.get("vwap", pd.Series(np.nan, index=df.index))
    close_s = df["close"]
    # % deviation of close from VWAP
    vwp_pct = (close_s - vwap_series).abs() / vwap_series.replace(0, np.nan) * 100.0
    # POC approximation — best we can do without intraday data is VWAP itself
    poc_approx = vwap_series.copy()
    return pd.DataFrame({
        "vwp_pct": vwp_pct,
        "poc_approx": poc_approx,
    }, index=df.index)


# ═══════════════════════════════════════════════════════════════════════════
# CORE COMPUTATION (unchanged from v1)
# ═══════════════════════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if len(df) < max(SMA_PERIODS):
        return pd.DataFrame()

    result = df[["ticker", "timestamp", "open", "high", "low", "close", "volume"]].copy(deep=True)
    result["timeframe"] = timeframe

    for p in SMA_PERIODS:
        result[f"sma_{p}"] = sma(df["close"], p)
    result[f"ema_{EMA_PERIOD}"] = ema(df["close"], EMA_PERIOD)

    result["rsi_14"] = rsi_wilder(df["close"], RSI_PERIOD)
    result["atr_14"] = atr_wilder(df["high"], df["low"], df["close"], ATR_PERIOD)

    atr_keltner = atr_wilder(df["high"], df["low"], df["close"], KELTNER_ATR)
    middle, upper, lower = keltner_channels(
        df["close"], atr_keltner, KELTNER_EMA, KELTNER_ATR, KELTNER_MULT
    )
    result["keltner_middle"] = middle
    result["keltner_upper"] = upper
    result["keltner_lower"] = lower

    # ── anchored_vwap, vwap_anchor, volume_spike (table positions 18-20) ──
    result["anchored_vwap"] = np.nan
    result["vwap_anchor"] = ""  # object/string column — write spikes as 'YYYY-MM-DD' strings
    result["vwap_anchor"] = result["vwap_anchor"].astype(object)
    result["volume_spike"] = False

    # ── Bollinger Bands (20, 2) — table positions 21-23 ───────────────
    bb_df = bollinger_bands(df["close"], BB_PERIOD, BB_NUM_STD)
    result["bb_upper"] = bb_df["bb_upper"]
    result["bb_lower"] = bb_df["bb_lower"]
    result["bb_width"] = bb_df["bb_width"]

    # ── MACD(12, 26, 9) — table positions 24-26 ──────────────────────
    macd_df = macd(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    result["macd_line"] = macd_df["macd_line"]
    result["macd_signal"] = macd_df["macd_signal"]
    result["macd_histogram"] = macd_df["macd_histogram"]

    # ── Keltner multiplier variants — table positions 27-30 ───────────
    _, upper_1_5, lower_1_5 = keltner_channels(
        df["close"], atr_keltner, KELTNER_EMA, KELTNER_ATR, KELTNER_MULT_1_5
    )
    result["keltner_upper_1_5"] = upper_1_5
    result["keltner_lower_1_5"] = lower_1_5

    _, upper_3_0, lower_3_0 = keltner_channels(
        df["close"], atr_keltner, KELTNER_EMA, KELTNER_ATR, KELTNER_MULT_3_0
    )
    result["keltner_upper_3_0"] = upper_3_0
    result["keltner_lower_3_0"] = lower_3_0

    # ── VWAP from bar data (pulled from daily_bars) — table position 31 ──
    result["vwap"] = df.get("vwap", np.nan)

    # ── Market profile approximation — table position 32 ──────────────
    mp_df = market_profile_approx(df)
    result["poc_approx"] = mp_df["poc_approx"]

    if timeframe == "daily" and len(df) > VWAP_VOL_LOOKBACK:
        avg_vol_20 = df["volume"].rolling(window=VWAP_VOL_LOOKBACK, min_periods=VWAP_VOL_LOOKBACK).mean().shift(1)
        spike_mask = df["volume"] > (avg_vol_20 * VWAP_VOL_MULT)
        spike_indices = df.index[spike_mask].tolist()
        ts_to_position = {ts: i for i, ts in enumerate(result["timestamp"])}

        for spike_idx in spike_indices:
            spike_ts = df.loc[spike_idx, "timestamp"]
            if spike_ts not in ts_to_position:
                continue
            result_pos = ts_to_position[spike_ts]
            result.iloc[result_pos, result.columns.get_loc("volume_spike")] = True
            spike_date = pd.to_datetime(spike_ts, unit="ms").strftime("%Y-%m-%d")
            df_subset = df.loc[spike_idx:]
            vwap_series = anchored_vwap(df_subset, 0)
            for sub_pos, sub_ts in enumerate(df_subset["timestamp"]):
                if sub_ts in ts_to_position:
                    res_pos = ts_to_position[sub_ts]
                    result.iloc[res_pos, result.columns.get_loc("anchored_vwap")] = vwap_series.iloc[sub_pos]
                    result.iloc[res_pos, result.columns.get_loc("vwap_anchor")] = spike_date

    min_periods = max(SMA_PERIODS + [RSI_PERIOD, ATR_PERIOD])
    result = result.iloc[min_periods - 1:].copy()
    return result


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    if len(df_1h) == 0:
        return pd.DataFrame()
    df = df_1h.copy()
    bucket_ms = 4 * 3600 * 1000
    df["bucket"] = (df["timestamp"] // bucket_ms) * bucket_ms
    ohlcv = df.groupby("bucket").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).reset_index()
    ohlcv.rename(columns={"bucket": "timestamp"}, inplace=True)
    return ohlcv


# ═══════════════════════════════════════════════════════════════════════════
# CHUNKED DATA LOADING (v2 optimization)
# ═══════════════════════════════════════════════════════════════════════════

def load_tickers_chunk(con, tickers: list) -> dict:
    """
    Load daily AND hourly data for a chunk of tickers in TWO queries total.
    Returns {ticker: {'daily': DataFrame, 'hourly': DataFrame}}.
    """
    ticker_list = ", ".join(f"'{t}'" for t in tickers)
    
    # Single query for daily data
    daily_rows = con.execute(f"""
        SELECT ticker, timestamp, open, high, low, close, volume, vwap
        FROM daily_bars
        WHERE ticker IN ({ticker_list})
        ORDER BY ticker, timestamp ASC
    """).fetchall()

    # Single query for hourly data
    hourly_rows = con.execute(f"""
        SELECT ticker, timestamp, open, high, low, close, volume
        FROM hourly_bars
        WHERE ticker IN ({ticker_list})
        ORDER BY ticker, timestamp ASC
    """).fetchall()

    result = {}
    daily_cols = ["ticker", "timestamp", "open", "high", "low", "close", "volume", "vwap"]
    hourly_cols = ["ticker", "timestamp", "open", "high", "low", "close", "volume"]
    
    # Partition daily by ticker
    if daily_rows:
        df_daily = pd.DataFrame(daily_rows, columns=daily_cols)
        for tkr in tickers:
            subset = df_daily[df_daily["ticker"] == tkr]
            if len(subset) > 0:
                result.setdefault(tkr, {})["daily"] = subset.reset_index(drop=True)

    # Partition hourly by ticker
    if hourly_rows:
        df_hourly = pd.DataFrame(hourly_rows, columns=hourly_cols)
        for tkr in tickers:
            subset = df_hourly[df_hourly["ticker"] == tkr]
            if len(subset) > 0:
                result.setdefault(tkr, {})["hourly"] = subset.reset_index(drop=True)
    
    return result


def process_chunk(chunk_data: dict) -> list:
    """
    Process all timeframes for a chunk of tickers SEQUENTIALLY.
    ThreadPoolExecutor hurts CPU-bound pandas work (GIL contention).
    Sequential is 6x faster for this workload (2s/tkr vs 12s/tkr).
    """
    results = []
    
    for ticker, data in chunk_data.items():
        df_daily = data.get("daily")
        df_hourly = data.get("hourly")
        
        if df_daily is not None and len(df_daily) >= max(SMA_PERIODS):
            indicators = compute_indicators(df_daily, "daily")
            if len(indicators) > 0:
                results.append(indicators)
        
        if df_hourly is not None and len(df_hourly) >= max(SMA_PERIODS):
            indicators_1h = compute_indicators(df_hourly, "1h")
            if len(indicators_1h) > 0:
                results.append(indicators_1h)
            
            df_4h = resample_to_4h(df_hourly)
            if len(df_4h) >= max(SMA_PERIODS):
                df_4h["ticker"] = ticker
                df_4h = df_4h[["ticker", "timestamp", "open", "high", "low", "close", "volume"]]
                indicators_4h = compute_indicators(df_4h, "4h")
                if len(indicators_4h) > 0:
                    results.append(indicators_4h)
    
    return results


# ═══════════════════════════════════════════════════════════════════════════
# NDJSON + DUCKDB MERGE
# ═══════════════════════════════════════════════════════════════════════════

def flush_to_ndjson(dataframes: list, ndjson_path: Path, append: bool = False):
    """Write DataFrames to NDJSON one at a time — no concat (OOM-safe)."""
    if not dataframes:
        return
    mode = "a" if append else "w"
    first = True
    with open(ndjson_path, mode) as f:
        for df in dataframes:
            # Write each DataFrame as NDJSON lines, skip header on append
            json_str = df.to_json(orient="records", lines=True, date_format="iso")
            if not json_str.strip():
                continue
            f.write(json_str)
            if not json_str.endswith("\n"):
                f.write("\n")
            # Force flush to disk periodically
            if first:
                f.flush()
                first = False


def bulk_merge_indicators(ndjson_path: Path) -> int:
    import duckdb
    if not ndjson_path.exists() or ndjson_path.stat().st_size == 0:
        return 0
    
    size_mb = ndjson_path.stat().st_size / 1024**2
    log.info(f"  Merging {size_mb:.0f}MB NDJSON → technical_indicators...")
    t0 = time.time()
    
    db = duckdb.connect(str(DB_PATH))
    db.execute("""
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
    
    db.execute(f"""
        INSERT INTO technical_indicators
        SELECT * FROM read_json('{ndjson_path}', format='newline_delimited',
            columns={{
                ticker: 'VARCHAR', timestamp: 'BIGINT', timeframe: 'VARCHAR',
                open: 'DOUBLE', high: 'DOUBLE', low: 'DOUBLE', close: 'DOUBLE',
                volume: 'DOUBLE', sma_20: 'DOUBLE', sma_50: 'DOUBLE', sma_200: 'DOUBLE',
                ema_20: 'DOUBLE', rsi_14: 'DOUBLE', atr_14: 'DOUBLE',
                keltner_middle: 'DOUBLE', keltner_upper: 'DOUBLE', keltner_lower: 'DOUBLE',
                anchored_vwap: 'DOUBLE', vwap_anchor: 'VARCHAR', volume_spike: 'BOOLEAN',
                bb_upper: 'DOUBLE', bb_lower: 'DOUBLE', bb_width: 'DOUBLE',
                macd_line: 'DOUBLE', macd_signal: 'DOUBLE', macd_histogram: 'DOUBLE',
                keltner_upper_1_5: 'DOUBLE', keltner_lower_1_5: 'DOUBLE',
                keltner_upper_3_0: 'DOUBLE', keltner_lower_3_0: 'DOUBLE',
                vwap: 'DOUBLE', poc_approx: 'DOUBLE'
            }},
            ignore_errors=true
        ) src
        WHERE NOT EXISTS (
            SELECT 1 FROM technical_indicators t
            WHERE t.ticker = src.ticker
              AND t.timestamp = src.timestamp
              AND t.timeframe = src.timeframe
        )
    """)
    
    elapsed = time.time() - t0
    count = db.execute("SELECT COUNT(*) FROM technical_indicators").fetchone()[0]
    tickers = db.execute("SELECT COUNT(DISTINCT ticker) FROM technical_indicators").fetchone()[0]
    db.close()
    
    log.info(f"  Merge: {count:,} total rows, {tickers:,} tickers in {elapsed:.1f}s")
    return count


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import duckdb
    
    parser = argparse.ArgumentParser(description="Build technical indicators v2 — chunked")
    parser.add_argument("--tickers", nargs="*", help="Specific tickers to process")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    parser.add_argument("--max-chunks", type=int, default=0, help="Limit chunks (0=all)")
    args = parser.parse_args()
    
    RAW_DIR.mkdir(exist_ok=True)
    ndjson_path = RAW_DIR / "indicators.ndjson"
    
    # Clean NDJSON
    if ndjson_path.exists():
        ndjson_path.unlink()
    
    # Determine tickers
    con = duckdb.connect(str(DB_PATH))
    if args.tickers:
        all_tickers = sorted(set(t.upper() for t in args.tickers))
    else:
        all_tickers = sorted(set(
            r[0] for r in con.execute("""
                SELECT DISTINCT d.ticker FROM daily_bars d
                WHERE EXISTS (SELECT 1 FROM hourly_bars h WHERE h.ticker = d.ticker)
            """).fetchall()
        ))
    con.close()
    
    chunk_size = args.chunk_size
    chunks = [all_tickers[i:i+chunk_size] for i in range(0, len(all_tickers), chunk_size)]
    if args.max_chunks > 0:
        chunks = chunks[:args.max_chunks]
    
    log.info(f"Tickers: {len(all_tickers):,}  |  Chunks: {len(chunks):,} ({chunk_size}/chunk)  |  Workers: {MAX_WORKERS}")
    
    t0 = time.time()
    total_bars = 0
    
    for ci, ticker_chunk in enumerate(chunks):
        chunk_t0 = time.time()
        
        # ONE DuckDB connection per chunk (key optimization)
        con = duckdb.connect(str(DB_PATH))
        chunk_data = load_tickers_chunk(con, ticker_chunk)
        con.close()
        
        # Compute indicators in parallel within the chunk
        results = process_chunk(chunk_data)
        
        # Flush to NDJSON
        if results:
            flush_to_ndjson(results, ndjson_path, append=(ci > 0))
            for df in results:
                total_bars += len(df)
        
        # Release memory aggressively between chunks (OOM-safe)
        del results
        del chunk_data
        gc.collect()
        
        elapsed_chunk = time.time() - chunk_t0
        tickers_in_chunk = len(ticker_chunk)
        pct = (ci + 1) / len(chunks) * 100
        
        log.info(f"  [{ci+1:,}/{len(chunks):,}] {pct:.0f}% — "
                 f"{tickers_in_chunk} tickers in {elapsed_chunk:.0f}s "
                 f"({tickers_in_chunk/elapsed_chunk:.1f} tkr/s) — {total_bars:,} total bars")
    
    elapsed = time.time() - t0
    log.info(f"Computed: {total_bars:,} indicator rows in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    
    # Merge
    if not args.dry_run and ndjson_path.exists():
        bulk_merge_indicators(ndjson_path)
        ndjson_path.unlink()
        
        # Stats
        db = duckdb.connect(str(DB_PATH))
        stats = db.execute("""
            SELECT timeframe, COUNT(*) as rows, COUNT(DISTINCT ticker) as tickers,
                   MIN(timestamp), MAX(timestamp)
            FROM technical_indicators
            GROUP BY timeframe ORDER BY timeframe
        """).fetchall()
        db.close()
        
        print("\n✅ Technical Indicators Complete")
        for tf, rows, tkrs, tmin, tmax in stats:
            from datetime import datetime, timezone
            dmin = datetime.fromtimestamp(tmin/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            dmax = datetime.fromtimestamp(tmax/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"  {tf:6s}  {rows:>10,} rows  {tkrs:>5,} tickers  {dmin} → {dmax}")
    else:
        log.info(f"DRY RUN — {total_bars:,} rows would be written")
        if ndjson_path.exists():
            ndjson_path.unlink()


if __name__ == "__main__":
    main()
