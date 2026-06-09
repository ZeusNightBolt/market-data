#!/usr/bin/env python3
"""
Chart: AAPL 4h — Keltner Bands + 200 SMA + Anchored VWAP
Reads raw data from DuckDB, computes indicators on the fly.
Saves to ~/market-data/charts/aapl_4h_keltner.png
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone

sys.path.insert(0, str(Path.home() / "market-data"))
from build_indicators import (
    sma, ema, atr_wilder, keltner_channels, anchored_vwap, resample_to_4h
)

# ── Data ──────────────────────────────────────────────────────────────
import duckdb
DB = Path.home() / "market-data" / "market_data.duckdb"
con = duckdb.connect(str(DB))

# Load AAPL hourly bars, resample to 4h
rows = con.execute("""
    SELECT ticker, timestamp, open, high, low, close, volume
    FROM hourly_bars WHERE ticker = 'AAPL'
    ORDER BY timestamp ASC
""").fetchall()
con.close()

df_1h = pd.DataFrame(rows, columns=["ticker", "timestamp", "open", "high", "low", "close", "volume"])
df_4h = resample_to_4h(df_1h)

# Focus on last 6 months for readability
cutoff_ts = int(datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
df = df_4h[df_4h["timestamp"] >= cutoff_ts].reset_index(drop=True)

# ── Indicators ────────────────────────────────────────────────────────
sma200 = sma(df["close"], 200)
atr10  = atr_wilder(df["high"], df["low"], df["close"], 10)
middle, upper, lower = keltner_channels(df["close"], atr10, 20, 10, 2.0)

# Anchored VWAP from most recent volume spike (>3x 20-day avg)
avg_vol = df["volume"].rolling(20, min_periods=20).mean().shift(1)
spike_mask = df["volume"] > (avg_vol * 3.0)
spike_indices = df.index[spike_mask].tolist()

avwap = pd.Series(np.nan, index=df.index)
avwap_anchor = None
if spike_indices:
    last_spike = spike_indices[-1]
    df_subset = df.loc[last_spike:]
    vwap_series = anchored_vwap(df_subset, 0)
    for sub_pos, orig_idx in enumerate(df_subset.index):
        avwap.iloc[orig_idx] = vwap_series.iloc[sub_pos]
    avwap_anchor = pd.to_datetime(df.loc[last_spike, "timestamp"], unit="ms").strftime("%Y-%m-%d")

# Convert timestamps to datetime for plotting
dates = pd.to_datetime(df["timestamp"], unit="ms")

# ── Plot ──────────────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9),
    gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
fig.patch.set_facecolor("#0d1117")

for ax in [ax1, ax2]:
    ax.set_facecolor("#0d1117")
    ax.tick_params(colors="#8b949e", labelsize=9)
    ax.grid(True, alpha=0.15, color="#30363d")
    for spine in ax.spines.values():
        spine.set_color("#30363d")

# ── Price panel ────────────────────────────────────────────────────
# Keltner bands (shaded channel)
ax1.fill_between(dates, lower, upper, alpha=0.12, color="#58a6ff", label="Keltner Channel")
ax1.plot(dates, upper, color="#58a6ff", linewidth=0.6, alpha=0.5)
ax1.plot(dates, lower, color="#58a6ff", linewidth=0.6, alpha=0.5)
ax1.plot(dates, middle, color="#79c0ff", linewidth=0.8, alpha=0.6, label="EMA 20 (Mid)")

# SMA 200
ax1.plot(dates, sma200, color="#d2a8ff", linewidth=1.2, alpha=0.9, label="SMA 200")

# Anchored VWAP
if spike_indices:
    ax1.plot(dates, avwap, color="#ff7b72", linewidth=1.5, linestyle="--",
             label=f"Anchored VWAP ({avwap_anchor})")

# Candlesticks (simplified as OHLC bars)
width = 0.6 / 24  # ~36 minutes in day units
for i in range(len(df)):
    color = "#3fb950" if df.loc[i, "close"] >= df.loc[i, "open"] else "#f85149"
    bar_width = 0.03
    ax1.plot([dates[i], dates[i]], [df.loc[i, "low"], df.loc[i, "high"]],
             color=color, linewidth=0.8)
    ax1.plot([dates[i] - pd.Timedelta(hours=1.5), dates[i] + pd.Timedelta(hours=1.5)],
             [df.loc[i, "open"], df.loc[i, "open"]], color=color, linewidth=2.5)
    ax1.plot([dates[i] - pd.Timedelta(hours=1.5), dates[i] + pd.Timedelta(hours=1.5)],
             [df.loc[i, "close"], df.loc[i, "close"]], color=color, linewidth=2.5)
    ax1.vlines(dates[i], df.loc[i, "open"], df.loc[i, "close"],
               color=color, linewidth=4.5)

# Volume spike markers
for spike_idx in spike_indices:
    ax1.scatter(dates[spike_idx], df.loc[spike_idx, "high"] * 1.002,
                marker="v", s=40, color="#ff7b72", zorder=5, alpha=0.8)

ax1.set_ylabel("Price ($)", color="#c9d1d9", fontsize=10, fontfamily="monospace")
ax1.legend(loc="upper left", fontsize=8, facecolor="#161b22", edgecolor="#30363d",
           labelcolor="#c9d1d9")
ax1.set_title("AAPL  —  4h Bars  |  Keltner(20,10,2.0)  |  SMA 200  |  Anchored VWAP",
              color="#c9d1d9", fontsize=13, fontfamily="monospace", pad=12,
              fontweight="bold")

# ── Volume panel ────────────────────────────────────────────────────
colors = ["#3fb950" if df.loc[i, "close"] >= df.loc[i, "open"] else "#f85149"
          for i in range(len(df))]
ax2.bar(dates, df["volume"] / 1e6, width=0.03, color=colors, alpha=0.7)
ax2.set_ylabel("Vol (M)", color="#8b949e", fontsize=9, fontfamily="monospace")
ax2.set_xlabel("", color="#8b949e", fontsize=9)

# Volume spike highlight
for spike_idx in spike_indices:
    ax2.axvline(dates[spike_idx], color="#ff7b72", linewidth=0.8, alpha=0.4, linestyle="--")

# Formatting
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
plt.xticks(rotation=0, ha="center")
fig.tight_layout(pad=1.5)

# Save
outdir = Path.home() / "market-data" / "charts"
outdir.mkdir(exist_ok=True)
outpath = outdir / "aapl_4h_keltner.png"
fig.savefig(outpath, dpi=150, facecolor="#0d1117", bbox_inches="tight")
plt.close()

print(f"✅ Chart saved: {outpath}")
print(f"   Bars: {len(df)}   Date range: {dates.iloc[0].strftime('%Y-%m-%d')} → {dates.iloc[-1].strftime('%Y-%m-%d')}")
print(f"   Keltner width: ${upper.iloc[-1]:.2f} – ${lower.iloc[-1]:.2f}")
print(f"   SMA 200: ${sma200.dropna().iloc[-1]:.2f}")
if avwap_anchor:
    print(f"   Anchored VWAP (from {avwap_anchor}): ${avwap.dropna().iloc[-1]:.2f}")
print(f"   Volume spikes: {len(spike_indices)}")
