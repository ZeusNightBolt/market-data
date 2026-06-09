# Technical Indicators Math Audit & Improvement Recommendations

**Date**: 2026-05-23  
**Audit scope**: `build_indicators.py`, `create_indicator_views.py`, `test_indicators.py`  
**Test status**: 57/57 passing ✅

---

## 1. MATH AUDIT — Per-Indicator Verification

### 1.1 SMA (`sma()`)
- **Implementation**: `series.rolling(window=period, min_periods=period).mean()`
- **Canonical formula**: Sum of last N closes / N
- **Verdict**: ✅ **Correct.** Standard simple moving average. Verified by test.

### 1.2 EMA (`ema()`)
- **Implementation**: `series.ewm(span=period, adjust=False, min_periods=period).mean()`
- **Canonical formula**: `EMA_today = α × price + (1-α) × EMA_yesterday` where `α = 2/(span+1)`
- **Verdict**: ✅ **Correct.** `adjust=False` gives the standard Wilder/TradingView recursive EMA (not the adjusted finite-history version). Verified by test (EMA(3) on [10,10,10,20,20] → idx3=15.0, idx4=17.5).

### 1.3 Wilder Smoothing (`wilder_smooth()`)
- **Implementation**: Seeds with SMA of first `period` values, then `vals[i] = (1-1/period) × vals[i-1] + (1/period) × raw[i]`
- **Canonical formula**: Same — Wilder's smoothed moving average used in RSI and ATR.
- **Verdict**: ✅ **Correct.** NaN-aware (carries forward on NaN input). Verified by test (14-period on [1..15] → idx13=7.5, idx14=112.5/14≈8.0357).

### 1.4 True Range (`true_range()`)
- **Implementation**: `max(H-L, |H-prev_C|, |L-prev_C|)`
- **Canonical formula**: Same — Wilder's True Range.
- **Verdict**: ✅ **Correct.** NaN-safe via pandas. Verified by test with 5-bar series giving [3,4,3,4,6].

### 1.5 RSI Wilder (`rsi_wilder()`)
- **Implementation**: Computes avg_gain/avg_loss via Wilder smoothing, then `RSI = 100 - 100/(1+RS)`. Fills NaN→100 (flat price = no losses = max strength).
- **Canonical formula**: `RSI = 100 - 100/(1 + avg_gain_14/avg_loss_14)`
- **Verdict**: ✅ **Correct with one note.** The `fillna(100)` for flat periods (0/0 division) is a design choice consistent with "no downward movement = max bullish strength." Some platforms return 50 for flat. This is semantically reasonable and matches Wilder's original interpretation where RSI=100 means no losses. Edge cases all verified (all-up→100, all-down→0, oscillating→~50).

### 1.6 ATR Wilder (`atr_wilder()`)
- **Implementation**: True Range → Wilder smoothed.
- **Canonical formula**: `ATR_14 = Wilder(TR, 14)`
- **Verdict**: ✅ **Correct.** Verified by test (constant range of 2 → ATR=2.0).

### 1.7 Keltner Channels (`keltner_channels()`)
- **Implementation**: `middle = EMA(close, 20)`, `upper = middle + ATR_10 × 2.0`, `lower = middle - ATR_10 × 2.0`
- **Canonical formula**: Linda Raschke's Keltner = EMA(close, 20) ± ATR(10) × multiplier. Original Chester Keltner used SMA(10) ± offset from H/L range. Both variants are valid.
- **Verdict**: ✅ **Correct.** Uses the modern EMA+ATR variant (Raschke style), which is more common in trading platforms. Parameters: EMA=20, ATR_period=10, mult=2.0 are standard.

### 1.8 Anchored VWAP (`anchored_vwap()`)
- **Implementation**: Typical price `(H+L+C)/3 × volume`, cumulative sum from anchor, `VWAP = cum_PV / cum_VOL`. Subtracts anchor_idx-1 baseline when anchor > 0.
- **Canonical formula**: `VWAP_anchored = Σ(price_i × vol_i) / Σ(vol_i)` from anchor point forward.
- **Verdict**: ✅ **Correct.** Anchor subtraction logic is correct. Verified by test (4-bar series, anchor at idx 1 → values 101, 101.6, 102.22).

### 1.9 Volume Spike Detection
- **Implementation** (in `compute_indicators`): `volume > avg_vol_20_prev × 3.0` where avg_vol_20_prev is rolling 20-day mean of previous 20 bars (excludes current).
- **Verdict**: ✅ **Correct.** Uses `shift(1)` to exclude current bar, avoiding look-ahead bias. Multiplier of 3.0 is reasonable.

### 1.10 SQL Views (`create_indicator_views.py`)
- **v_indicators_daily**: SMA 20/50/200 via window functions, volume spike via `ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING` (excludes current bar correctly). ✅
- **v_indicators_1h**: SMA 20/50/200 on hourly bars. ✅
- **v_indicators_4h**: Resamples from hourly using integer math `timestamp / 14400000 * 14400000` (4h = 14,400,000 ms), deduplicates by row_number. ✅

---

## 2. MISSING INDICATORS — Ranked by Value

### Tier 1: High Signal / Low Implementation Cost

| Rank | Indicator | Signal Value | Implementation Complexity | Notes |
|------|-----------|-------------|--------------------------|-------|
| **1** | **Bollinger Bands** | Trend/volatility + mean reversion | **Trivial** — reuse SMA, add std dev | Complements Keltner (ATR-based). BB uses std dev — different market regime signal. `upper/lower = SMA(20) ± 2×σ(20)`. Pairs with existing SMA views. |
| **2** | **MACD** | Trend momentum + divergences | **Low** — EMA(12)-EMA(26) + signal=EMA(9) + histogram | Already have EMA infrastructure. Standard trio: MACD line, signal line, histogram. Just 3 extra EMA calls. |
| **3** | **ADX / DI+ / DI-** | Trend strength (non-directional) + direction | **Medium** — requires True Range + directional movement + Wilder(14) × 3 | Foundational. Already have TR and wilder_smooth. ADX above 25 = trending (regardless of direction). DI+/DI- crossovers for entries. |
| **4** | **ROC (Rate of Change)** | Pure momentum, zero-lag | **Trivial** — `(close/close[-n] - 1) × 100` | 12-period ROC is standard. Different from RSI (which is bounded 0-100). ROC is unbounded, sensitive to velocity. |

### Tier 2: Volume-Aware / Confirming Signals

| Rank | Indicator | Signal Value | Implementation Complexity | Notes |
|------|-----------|-------------|--------------------------|-------|
| **5** | **OBV (On-Balance Volume)** | Volume-price divergence confirmation | **Trivial** — cumulative sum with sign from close direction | `OBV += volume if close > prev_close else -volume`. Single pass, O(n). Daily_bars already has all inputs. OBV divergences often precede price reversals. |
| **6** | **MFI (Money Flow Index)** | Volume-weighted RSI — overbought/oversold with volume confirmation | **Low** — Typical price × volume as "money flow", then 14-period ratio like RSI | More reliable than RSI alone in high-volume regimes. RSI might show overbought while MFI shows continued buying pressure. |
| **7** | **Stochastic Oscillator** | Short-term overbought/oversold + divergence | **Medium** — `%K = (C-L14)/(H14-L14)×100`, `%D = SMA(%K, 3)` | Pairs with RSI for confirmation. Fast line (%K) + slow line (%D). Good for range-bound markets. |

### Tier 3: Higher Complexity / Specialized

| Rank | Indicator | Signal Value | Implementation Complexity | Notes |
|------|-----------|-------------|--------------------------|-------|
| **8** | **Ichimoku Cloud** | Multi-factor trend + support/resistance | **High** — 5 lines (Tenkan, Kijun, Senkou A/B, Chikou), each with different lookbacks | Excellent for trend identification but complex to implement and maintain. Very popular in crypto/forex. Better as a Phase 2 add. |
| **9** | **Parabolic SAR** | Trailing stop + trend reversal | **Medium** — iterative, needs acceleration factor logic | Good for exits. Not as high priority as above items. |

---

## 3. PARAMETER VARIANT RECOMMENDATIONS — Ranked by Value

### Tier 1: Highest Signal Diversity per Cost

| Rank | Variant | Rationale | Implementation Notes |
|------|---------|-----------|---------------------|
| **1** | **RSI(2)** | Ultra-short-term mean reversion (Larry Connors). RSI<10 = extreme oversold bounce. Huge signal diversity vs RSI(14). | Add `rsi_2` column. Same `rsi_wilder(close, 2)`. |
| **2** | **EMA(50), EMA(200)** | Currently only EMA(20). EMA(50) and EMA(200) are standard trend gauges. | Add `ema_50`, `ema_200`. Trivial addition. |
| **3** | **SMA(10)** | Short-term trend, commonly used with SMA(20) for crossovers. | Add `sma_10` to SMA_PERIODS list. |
| **4** | **ATR(5)** | Fast volatility gauge — responds to volatility regime changes faster than ATR(14). | Add `atr_5` column. Useful for tighter stop placement in high-vol environments. |
| **5** | **Keltner(1.5), Keltner(3.0)** | Multiplier variants: 1.5 = squeeze detection (inside BB+Keltner), 3.0 = extreme breakout filter. | Add `keltner_upper_1_5` / `keltner_lower_1_5` and `keltner_upper_3_0` / `keltner_lower_3_0`. |

### Tier 2: Good but Lower Priority

| Rank | Variant | Rationale |
|------|---------|-----------|
| **6** | **RSI(7)** | Intermediate momentum — bridges RSI(2) (noise) and RSI(14) (slow). |
| **7** | **RSI(21)** | Slower oscillator — fewer signals, higher quality. Pairs with 21-period lookback systems. |
| **8** | **EMA(9)** | Very short-term trend — popular for intraday and crypto traders. Pairs with MACD signal line. |
| **9** | **SMA(100)** | Medium-term trend. Already have 20/50/200; 100 fills the gap. |

### Tier 3: Specialty

| Rank | Variant | Rationale |
|------|---------|-----------|
| **10** | **ATR(21)** | One-month volatility horizon — useful for swing trading stops. |
| **11** | **Keltner(10, 1.5)** | Tighter squeeze with shorter ATR period. |

---

## 4. RECOMMENDED IMPLEMENTATION ORDER

### Phase 1: Immediate (Days 1-2) — Max Alpha / Min Cost

```
1a. Add RSI(2) to compute_indicators + technical_indicators table
    - 1 line change: result["rsi_2"] = rsi_wilder(df["close"], 2)
    - Proven alpha source (Connors RSI)

1b. Add EMA(50) and EMA(200)
    - Change EMA_PERIODS = [20, 50, 200] (from single EMA_PERIOD=20)
    - Loop like SMA: for p in EMA_PERIODS: result[f"ema_{p}"] = ema(df["close"], p)
    - Provides the full trend-tracking EMA suite

1c. Add SMA(10) to SMA_PERIODS = [10, 20, 50, 200]
    - Completes the standard SMA ladder (10/20/50/200)

1d. Add ATR(5) alongside ATR(14)
    - Add ATR_PERIODS = [5, 14] and loop
    - Short-term volatility for tighter stop placement
```

### Phase 2: Week 1-2 — Bollinger Bands + MACD

```
2a. Bollinger Bands (SMA 20 ± 2σ)
    - Reuses SMA(20) already computed
    - Add bb_upper_20, bb_lower_20 (or bb_upper / bb_lower)
    - Compute: rolling_std = close.rolling(20).std()
    - Upper = sma_20 + 2*rolling_std, Lower = sma_20 - 2*rolling_std
    - BB width = (upper-lower)/middle = measures volatility regime

2b. MACD (12, 26, 9)
    - macd_line = EMA(12) - EMA(26)
    - macd_signal = EMA(macd_line, 9)
    - macd_histogram = macd_line - macd_signal
    - Requires EMA(12) and EMA(26) columns — add to EMA_PERIODS

2c. Keltner multiplier variants (1.5, 3.0)
    - Reuses existing atr_keltner (ATR 10) and EMA 20 middle
    - Just compute upper/lower with different multipliers
```

### Phase 3: Week 2-3 — Volume + Momentum Indicators

```
3a. OBV (On-Balance Volume)
    - Single-pass cumulative sum
    - OBV divergences are high-confidence reversal signals

3b. ROC(12) — Rate of Change
    - roc_12 = (close / close.shift(12) - 1) * 100
    - Pure momentum, pairs with RSI

3c. MFI(14) — Money Flow Index
    - Volume-weighted RSI analog
    - Confirms or contradicts RSI signals
```

### Phase 4: Month 2 — Directional Movement

```
4a. ADX(14) with DI+ and DI-
    - Requires: +DM, -DM, TR, then Wilder smoothing on each
    - ADX = 100 × EMA(abs(DI+ - DI-) / (DI+ + DI-), smoothing)
    - The gold standard for trend strength measurement

4b. Stochastic Oscillator (14, 3, 3)
    - %K and %D lines
    - Classic overbought/oversold with divergence detection
```

### Phase 5: Month 2+ — Advanced

```
5a. Ichimoku Cloud
    - Tenkan-sen (9), Kijun-sen (26), Senkou Span A/B (52), Chikou Span (26)
    - High implementation cost but comprehensive trend system

5b. Parabolic SAR
    - Iterative trailing stop
```

---

## 5. TECHNICAL DEBT / EDGE CASE NOTES

### 5.1 RSI Edge Case Behavior
- **Current**: `fillna(100)` when both avg_gain and avg_loss are 0 (flat prices).
- **Trade-off**: This treats flat price as "max bullish" (no losses). More neutral would be 50 (equal gain/loss).
- **Recommendation**: Keep current behavior but document it. Changing to 50 would be more theoretically neutral but less useful (flat=50 gives no signal).

### 5.2 Keltner Channel: ATR Period Mismatch
- **In `build_indicators.py`**: Keltner uses its own ATR(10) computed separately from the main ATR(14) column. The `atr_keltner` variable at line 153 is a fresh ATR(10) computation.
- **In `refresh_latest_daily_indicators.py`**: Keltner reuses the same `atr10` computed independently, matching the main script. ✅ Consistent.
- **No issue** — just confirming both paths maintain the ATR(10) calculation for Keltner.

### 5.3 SQL View SMA Uses ROWS Not Calendar Days
- Views use `ROWS BETWEEN N PRECEDING` — this is trade-day-based, not calendar-day-based.
- For daily bars with gaps (weekends/holidays), a 20-bar SMA spans ~28 calendar days on average.
- **This is correct** for financial SMA — bars, not calendar days, are the standard unit.

### 5.4 4H Resample — Boundary Handling
- The `timestamp / 14400000 * 14400000` integer math floors to nearest 4h boundary.
- This is correct but depends on the first hourly bar in each bucket having the correct open. The view uses `FIRST_VALUE` with `ORDER BY timestamp` which correctly picks the earliest bar in the bucket.

### 5.5 Volume Spike — Only on Daily
- Volume spike detection runs only for `timeframe == 'daily'` (line 165).
- 1h and 4h timeframes have no volume spike detection.
- **Recommendation**: Add spike detection to 1h and 4h with adjusted multipliers (e.g., 5× for 1h given higher noise).

---

## 6. SUMMARY

| Category | Status / Action |
|----------|----------------|
| **Existing math** | All 7 indicator functions + 1 detection method are **correct** and verified by 57 passing tests. |
| **SQL views** | SMA windows in all 3 views are correct. Volume spike excludes current bar (no look-ahead). |
| **Highest-impact gap** | Bollinger Bands (pairs with Keltner for squeeze plays). |
| **Easiest quick win** | RSI(2) + EMA(50,200) + SMA(10) — ~5 lines changed, massive signal diversity. |
| **Most valuable new indicator** | MACD (trivial with existing EMA infrastructure). |
| **Volume-confirming gap** | OBV (one pass, huge divergence signal value). |
| **Long-term add** | ADX/DI for trend strength — foundational for any directional system. |

**Net assessment**: The current system is mathematically sound with rigorous testing. The main opportunity is breadth — adding 8-10 indicators and parameter variants would cover ~90% of standard quantitative strategies with minimal additional computation cost (most are O(n) single-pass operations).
