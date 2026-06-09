#!/usr/bin/env python3
"""
Unit tests for build_indicators.py — validates all mathematical models.
Run: python3 test_indicators.py
"""

import sys, os
from pathlib import Path
import numpy as np
import pandas as pd

# Import indicator functions from the main script
sys.path.insert(0, str(Path.home() / "market-data"))
from build_indicators import (
    true_range, wilder_smooth, sma, ema, rsi_wilder, atr_wilder,
    keltner_channels, anchored_vwap, resample_to_4h, compute_indicators
)

PASS = 0
FAIL = 0

def t(name, actual, expected, tol=1e-10):
    global PASS, FAIL
    try:
        # Handle boolean comparisons separately (numpy bool_ vs Python bool)
        if isinstance(expected, bool):
            if bool(actual) == expected:
                PASS += 1
                print(f"  ✅ {name}")
                return
            else:
                raise AssertionError(f"got {actual}, expected {expected}")
        if isinstance(expected, str):
            if str(actual) == expected:
                PASS += 1
                print(f"  ✅ {name}")
                return
            else:
                raise AssertionError(f"got '{actual}', expected '{expected}'")
        if isinstance(expected, pd.Series):
            mask = expected.notna()
            if mask.sum() == 0:
                raise AssertionError("no non-NaN values to compare")
            diff = (actual[mask] - expected[mask]).abs().max()
            assert diff < tol, f"max diff = {diff}"
        elif isinstance(expected, (list, np.ndarray)):
            a = np.array(actual) if not isinstance(actual, np.ndarray) else actual
            e = np.array(expected)
            mask = ~np.isnan(e)
            assert mask.sum() > 0, "no non-NaN values"
            assert np.abs(a[mask] - e[mask]).max() < tol, f"max diff = {np.abs(a[mask]-e[mask]).max()}"
        elif expected is None:
            assert actual is None or (isinstance(actual, float) and np.isnan(actual)), f"expected None/NaN, got {actual}"
        else:
            assert abs(actual - expected) < tol, f"got {actual}, expected {expected}"
        PASS += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST: true_range
# ═══════════════════════════════════════════════════════════════════════════

def test_true_range():
    print("\n── true_range ──")
    close = pd.Series([100, 102, 101, 99, 103])
    high  = pd.Series([102, 104, 103, 101, 105])
    low   = pd.Series([99,  101, 100,  97, 101])
    
    tr = true_range(high, low, close)
    
    # Bar 0: TR = H-L = 102-99 = 3  (first bar, no prev close)
    # Bar 1: max(104-101=3, |104-100|=4, |101-100|=1) = 4
    # Bar 2: max(103-100=3, |103-102|=1, |100-102|=2) = 3
    # Bar 3: max(101-97=4,  |101-101|=0, |97-101|=4)  = 4
    # Bar 4: max(105-101=4, |105-99|=6,  |101-99|=2)  = 6
    expected = pd.Series([3, 4, 3, 4, 6], dtype=float)
    expected.index = close.index
    t("TR: basic 5-bar series", tr, expected)
    
    # Edge: NaN in prices
    close_nan = pd.Series([100, np.nan, 101])
    high_nan  = pd.Series([102, np.nan, 103])
    low_nan   = pd.Series([99,  np.nan, 100])
    tr_nan = true_range(high_nan, low_nan, close_nan)
    t("TR: handles NaN", tr_nan.iloc[0], 3.0)
    assert np.isnan(tr_nan.iloc[1]), "bar 1 should be NaN"


# ═══════════════════════════════════════════════════════════════════════════
# TEST: sma
# ═══════════════════════════════════════════════════════════════════════════

def test_sma():
    print("\n── sma ──")
    s = pd.Series([10, 20, 30, 40, 50])
    
    # SMA(3)
    result = sma(s, 3)
    expected = pd.Series([np.nan, np.nan, 20, 30, 40])
    expected.index = s.index
    t("SMA(3) basic", result, expected)
    
    # SMA(2) — shorter period
    result2 = sma(s, 2)
    expected2 = pd.Series([np.nan, 15, 25, 35, 45])
    expected2.index = s.index
    t("SMA(2) basic", result2, expected2)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: ema
# ═══════════════════════════════════════════════════════════════════════════

def test_ema():
    print("\n── ema ──")
    s = pd.Series([10, 10, 10, 20, 20])
    
    # EMA(3): α=2/(3+1)=0.5
    # EMA_2 = SMA(3) = 10
    # EMA_3 = 0.5*20 + 0.5*10 = 15
    # EMA_4 = 0.5*20 + 0.5*15 = 17.5
    result = ema(s, 3)
    t("EMA(3) value at idx 3", result.iloc[3], 15.0)
    t("EMA(3) value at idx 4", result.iloc[4], 17.5)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: wilder_smooth
# ═══════════════════════════════════════════════════════════════════════════

def test_wilder_smooth():
    print("\n── wilder_smooth ──")
    # Constant series: value should converge to constant
    s = pd.Series([5.0] * 20)
    result = wilder_smooth(s, 14)
    t("Wilder(14) on constant [5]", result.iloc[-1], 5.0)
    
    # Simple increasing series: manually verify first smoothed value
    s2 = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    result2 = wilder_smooth(s2, 14)
    # First value (idx 13) = mean of first 14 = (1+14)*14/2/14 = 7.5
    t("Wilder(14) first smoothed value", result2.iloc[13], 7.5)
    # Second value = 7.5 * 13/14 + 15/14 = (97.5 + 15)/14 = 112.5/14 ≈ 8.0357
    t("Wilder(14) second smoothed value", result2.iloc[14], 112.5/14)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: rsi_wilder
# ═══════════════════════════════════════════════════════════════════════════

def test_rsi_wilder():
    print("\n── rsi_wilder ──")
    
    # All gains, no losses → RSI = 100
    close_up = pd.Series(list(range(10, 110)))
    rsi_up = rsi_wilder(close_up, 14)
    t("RSI: all up days → 100", rsi_up.iloc[-1], 100.0)
    
    # All losses, no gains → RSI = 0
    close_down = pd.Series(list(range(100, 0, -1)))
    rsi_down = rsi_wilder(close_down, 14)
    t("RSI: all down days → 0", rsi_down.iloc[-1], 0.0)
    
    # Flat → RSI = undefined (should be NaN or 50?)
    # With Wilder, if both avg_gain and avg_loss are 0, RS = 0/0 → inf → RSI = 100
    # Actually: when all deltas are 0, gain and loss are all 0, wilder_smooth gives all 0,
    # rs = 0/0 → NaN → rsi = 100 - 100/NaN → NaN → fillna(100) → 100
    close_flat = pd.Series([50.0] * 30)
    rsi_flat = rsi_wilder(close_flat, 14)
    t("RSI: flat → 100 (no movement is max strength)", rsi_flat.iloc[-1], 100.0)
    
    # Oscillating: 14 up, 14 down, repeat
    # Gains avg ≈ losses avg → RSI should be near 50
    prices = []
    val = 100.0
    for i in range(50):
        if i % 2 == 0:
            val += 0.5
        else:
            val -= 0.5
        prices.append(val)
    close_osc = pd.Series(prices)
    rsi_osc = rsi_wilder(close_osc, 14)
    osc_val = float(rsi_osc.iloc[-1])
    t("RSI: oscillating → near 50", abs(osc_val - 50) < 10, True, tol=0)
    
    # Known values from TradingView-like calculation:
    # Using manually verified Wilder RSI
    known_close = pd.Series([
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
        46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
        46.22, 45.64, 46.21, 46.25, 45.71, 46.45
    ])
    known_rsi = rsi_wilder(known_close, 14)
    # First RSI value at idx 13 should be meaningful
    t("RSI: known data first RSI populated", not np.isnan(known_rsi.iloc[14]), True, tol=0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: atr_wilder
# ═══════════════════════════════════════════════════════════════════════════

def test_atr_wilder():
    print("\n── atr_wilder ──")
    
    # Constant range: TR = 2 every bar
    # All bars identical: H=52, L=50, C=51 → TR = max(2, |52-51|=1, |50-51|=1) = 2
    high  = pd.Series([52.0] * 20)
    low   = pd.Series([50.0] * 20)
    close = pd.Series([51.0] * 20)
    
    atr = atr_wilder(high, low, close, 14)
    # TR = H-L = 2 for all
    # First ATR = mean([2]*14) = 2.0
    t("ATR(14) constant range → 2.0", atr.iloc[-1], 2.0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: keltner_channels
# ═══════════════════════════════════════════════════════════════════════════

def test_keltner_channels():
    print("\n── keltner_channels ──")
    
    close = pd.Series([100.0] * 40)
    atr_in = pd.Series([2.0] * 40)
    
    middle, upper, lower = keltner_channels(close, atr_in, ema_period=20, atr_period=10, multiplier=2.0)
    
    # Close = 100, EMA(20) = 100, ATR(10) = 2
    # Middle = 100, Upper = 100 + 2*2 = 104, Lower = 100 - 2*2 = 96
    t("Keltner: middle = 100", middle.iloc[-1], 100.0)
    t("Keltner: upper = 104",  upper.iloc[-1],  104.0)
    t("Keltner: lower = 96",   lower.iloc[-1],  96.0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: anchored_vwap
# ═══════════════════════════════════════════════════════════════════════════

def test_anchored_vwap():
    print("\n── anchored_vwap ──")
    
    df = pd.DataFrame({
        "high":   [102, 103, 104, 105],
        "low":    [98,  99,  100, 101],
        "close":  [100, 101, 102, 103],
        "volume": [100, 200, 300, 400],
    })
    
    # Anchor at idx 1 (second bar)
    vwap = anchored_vwap(df, 1)
    
    # Bar 0: NaN (before anchor)
    t("AVWAP: NaN before anchor", float(np.isnan(vwap.iloc[0])), True, tol=0)
    
    # Bar 1: TP=(103+99+101)/3=101, V=200, VWAP = 101
    t("AVWAP: anchor bar", vwap.iloc[1], 101.0)
    
    # Bar 2: cumulative from anchor: (101*200 + 102*300)/(200+300) = (20200+30600)/500 = 50800/500 = 101.6
    t("AVWAP: bar after anchor", vwap.iloc[2], 101.6)
    
    # Bar 3: (50800 + 103*400)/(500+400) = (50800+41200)/900 = 92000/900 ≈ 102.222...
    t("AVWAP: two bars after anchor", round(vwap.iloc[3], 6), round(92000/900, 6))


# ═══════════════════════════════════════════════════════════════════════════
# TEST: resample_to_4h
# ═══════════════════════════════════════════════════════════════════════════

def test_resample_to_4h():
    print("\n── resample_to_4h ──")
    
    # Create 8 hourly bars → 2 four-hour buckets
    base = 1622534400000  # 2021-06-01 00:00 UTC
    df = pd.DataFrame({
        "ticker": ["AAPL"] * 8,
        "timestamp": [base + i * 3600_000 for i in range(8)],
        "open":  [100, 101, 102, 103, 104, 105, 106, 107],
        "high":  [102, 103, 104, 105, 106, 107, 108, 109],
        "low":   [99,  100, 101, 102, 103, 104, 105, 106],
        "close": [101, 102, 103, 104, 105, 106, 107, 108],
        "volume":[10,  20,  30,  40,  50,  60,  70,  80],
    })
    
    result = resample_to_4h(df)
    
    # Bucket 0: bars 0-3 (00:00-03:00)
    # open=100, high=105, low=99, close=104, volume=10+20+30+40=100
    t("4h: 2 buckets", len(result), 2)
    t("4h: bucket 0 open", result.iloc[0]["open"], 100.0)
    t("4h: bucket 0 high", result.iloc[0]["high"], 105.0)
    t("4h: bucket 0 low",  result.iloc[0]["low"], 99.0)
    t("4h: bucket 0 close", result.iloc[0]["close"], 104.0)
    t("4h: bucket 0 volume", result.iloc[0]["volume"], 100.0)
    
    # Bucket 1: bars 4-7
    t("4h: bucket 1 open", result.iloc[1]["open"], 104.0)
    t("4h: bucket 1 close", result.iloc[1]["close"], 108.0)
    t("4h: bucket 1 volume", result.iloc[1]["volume"], 260.0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: compute_indicators — integration
# ═══════════════════════════════════════════════════════════════════════════

def test_compute_indicators():
    print("\n── compute_indicators (integration) ──")
    
    # Build 300 bars of synthetic data (enough for SMA 200)
    n = 300
    np.random.seed(42)
    close = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5))
    high  = close + np.abs(np.random.randn(n) * 0.3)
    low   = close - np.abs(np.random.randn(n) * 0.3)
    volume = pd.Series(np.random.randint(1_000_000, 10_000_000, n))
    base_ts = 1622534400000
    timestamps = [base_ts + i * 86400_000 for i in range(n)]
    
    df = pd.DataFrame({
        "ticker": ["TEST"] * n,
        "timestamp": timestamps,
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    
    result = compute_indicators(df, "daily")
    
    # Verify columns exist
    required_cols = ["ticker", "timestamp", "timeframe", "open", "high", "low",
                     "close", "volume", "sma_20", "sma_50", "sma_200", "ema_20",
                     "rsi_14", "atr_14", "keltner_middle", "keltner_upper",
                     "keltner_lower", "anchored_vwap", "vwap_anchor", "volume_spike"]
    for col in required_cols:
        t(f"  has column '{col}'", col in result.columns, True, tol=0)
    
    # Verify no NaN in key indicators after warmup (bar 199 = 200th bar)
    last_row = result.iloc[-1]
    t("  sma_200 not NaN at end", not np.isnan(last_row["sma_200"]), True, tol=0)
    t("  rsi_14 not NaN at end", not np.isnan(last_row["rsi_14"]), True, tol=0)
    t("  atr_14 not NaN at end", not np.isnan(last_row["atr_14"]), True, tol=0)
    t("  timeframe=daily", last_row["timeframe"], "daily")
    
    # RSI should be between 0 and 100
    rsi_vals = result["rsi_14"].dropna()
    t("  RSI ∈ [0, 100]", (rsi_vals >= 0).all() and (rsi_vals <= 100).all(), True, tol=0)
    
    # SMA consistency: SMA(200) at idx 199 should be close to SMA manually
    manual_sma200 = close.iloc[:200].mean()
    t("  sma_200 matches manual", abs(result.iloc[0]["sma_200"] - manual_sma200) < 0.1, True, tol=0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: macd
# ═══════════════════════════════════════════════════════════════════════════

def test_macd():
    print("\n── macd ──")
    from build_indicators import macd
    close = pd.Series([10.0 + i * 0.1 for i in range(100)])
    result = macd(close, fast=12, slow=26, signal=9)
    # All three columns present
    for col in ["macd_line", "macd_signal", "macd_histogram"]:
        t(f"  has '{col}'", col in result.columns, True, tol=0)
    # histogram = line - signal for last row
    last = result.iloc[-1]
    hist = last["macd_line"] - last["macd_signal"]
    t("  histogram = line - signal", abs(last["macd_histogram"] - hist) < 0.001, True, tol=0)
    # First N bars should be NaN (warmup)
    t("  first 25 rows NaN for macd_line", result["macd_line"].iloc[:25].isna().all(), True, tol=0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: bollinger_bands
# ═══════════════════════════════════════════════════════════════════════════

def test_bollinger_bands():
    print("\n── bollinger_bands ──")
    from build_indicators import bollinger_bands
    close = pd.Series([100.0] * 40)
    result = bollinger_bands(close, period=20, num_std=2.0)
    # Upper > Lower always (after warmup, with flat prices)
    valid = result.dropna()
    t("  bb_upper > bb_lower", (valid["bb_upper"] >= valid["bb_lower"]).all(), True, tol=0)
    t("  bb_width >= 0", (valid["bb_width"] >= 0).all(), True, tol=0)
    # With constant prices, bands should be flat (zero width)
    t("  bb_width ≈ 0 for flat prices", valid["bb_width"].iloc[-1] < 0.001, True, tol=0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: keltner multiplier variants
# ═══════════════════════════════════════════════════════════════════════════

def test_keltner_variants():
    print("\n── keltner multiplier variants ──")
    from build_indicators import keltner_channels, atr_wilder, true_range
    close  = pd.Series([100.0] * 40)
    high   = pd.Series([102.0] * 40)
    low    = pd.Series([98.0] * 40)
    atr10 = atr_wilder(high, low, close, 10)
    # Keltner with different multipliers (new signature: close, atr, ...)
    for mult in [1.5, 2.0, 3.0]:
        mid, up, lo = keltner_channels(close, atr10, multiplier=mult)
        width = up.iloc[-1] - lo.iloc[-1]
        expected = 2 * mult * 4.0  # TR=4, ATR10→4, width = 2*mult*ATR
        t(f"  mult={mult}: width ≈ {expected:.1f}", abs(width - expected) < 0.02, True, tol=0)

    # Width scaling: 1.5× < 2.0× < 3.0×
    _, u15, l15 = keltner_channels(close, atr10, multiplier=1.5)
    _, u20, l20 = keltner_channels(close, atr10, multiplier=2.0)
    _, u30, l30 = keltner_channels(close, atr10, multiplier=3.0)
    w15 = u15.iloc[-1] - l15.iloc[-1]
    w20 = u20.iloc[-1] - l20.iloc[-1]
    w30 = u30.iloc[-1] - l30.iloc[-1]
    t("  width 1.5× < 2.0× < 3.0×", w15 < w20 < w30, True, tol=0)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: Pivot Points (SQL view — verify via DuckDB)
# ═══════════════════════════════════════════════════════════════════════════

def test_pivot_points():
    print("\n── pivot_points ──")
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE daily_bars (ticker VARCHAR, timestamp BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, vwap DOUBLE, transactions INTEGER)
    """)
    con.execute("INSERT INTO daily_bars VALUES ('AAPL', 100, 9, 12, 8, 10, 100, 10, 0)")
    con.execute("INSERT INTO daily_bars VALUES ('AAPL', 200, 10, 11, 9, 10.5, 200, 10.5, 0)")
    
    pivots = con.execute("""
        WITH prior AS (
            SELECT ticker, timestamp, LAG(high,1) OVER w AS ph, LAG(low,1) OVER w AS pl,
                   LAG(close,1) OVER w AS pc, open, high, low, close
            FROM daily_bars
            WINDOW w AS (PARTITION BY ticker ORDER BY timestamp)
        )
        SELECT (ph+pl+pc)/3.0 AS pp, 2*(ph+pl+pc)/3.0-pl AS r1, 2*(ph+pl+pc)/3.0-ph AS s1
        FROM prior WHERE ph IS NOT NULL
    """).fetchone()
    
    # Day 1: H=12, L=8, C=10 → PP=(12+8+10)/3=10, R1=2*10-8=12, S1=2*10-12=8
    pp, r1, s1 = pivots
    t("  PP = 10", abs(pp - 10.0) < 0.01, True, tol=0)
    t("  R1 = 12", abs(r1 - 12.0) < 0.01, True, tol=0)
    t("  S1 = 8",  abs(s1 - 8.0)  < 0.01, True, tol=0)
    con.close()


if __name__ == "__main__":
    print("=" * 60)
    print("build_indicators.py — Unit Tests")
    print("=" * 60)
    
    test_true_range()
    test_sma()
    test_ema()
    test_wilder_smooth()
    test_rsi_wilder()
    test_atr_wilder()
    test_keltner_channels()
    test_anchored_vwap()
    test_resample_to_4h()
    test_compute_indicators()
    test_macd()
    test_bollinger_bands()
    test_keltner_variants()
    test_pivot_points()
    
    print(f"\n{'='*60}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        sys.exit(1)
