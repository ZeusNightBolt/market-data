---
title: Polygon Daily Refresh Optimization and Reverification — 2026-05-29/30
type: reference
project: market-data-warehouse
created: 2026-05-29T23:45:37-04:00
updated: 2026-05-30T08:08:33-04:00
tags: [polygon, daily-refresh, duckdb, postmortem, cron, audit]
---

# Polygon Daily Refresh Optimization and Reverification — 2026-05-29/30

This is the local market-data copy of the Hermes-OS project documentation at `~/.hermes/os/projects/market-data-warehouse/docs/polygon-daily-refresh-optimization-2026-05-29.md`.

## Status

Re-verified and hardened on 2026-05-30T08:08:33-04:00 after the production cron was re-run through the retry wrapper and strict audit passed.

## Postmortem lesson applied

Source: `~/market-data/references/postmortem-2026-05-26-gateway-hang.md`.

Known root cause from the postmortem: Polygon refreshes can hang indefinitely if API socket reads trickle data and the agent runs a long pipeline as a foreground terminal command without hard wall-clock timeouts. Prevention requirements:

- Run long refreshes via cron or `terminal(background=true, notify_on_complete=true)`, not a blocking foreground agent call.
- Wrap each pipeline step in `timeout --signal=KILL`.
- Catch Python `as_completed()` timeouts and write resumable partial outputs.
- Prefer incremental, stale-aware steps over full recomputes.
- Keep cron verification strict enough to fail on stale/missing core outputs, not just print a report.

## Canonical runner

Production cron runner: `~/.hermes/scripts/daily_warehouse_refresh_retry.sh`, which invokes canonical inner runner `~/.hermes/scripts/daily_warehouse_refresh.sh`.

- Step 1: `daily_append.py` wrapped with a 45-minute hard timeout.
- Step 2: `pull_ticker_details.py --stale-days ${DETAILS_STALE_DAYS:-30}` wrapped with a 60-minute hard timeout. Thirty days is intentional: the stale 7-day cadence tried to re-pull 985 stale reference rows plus 107 permanently missing/delisted tickers, causing the cron to exceed the calibrated daily window.
- Step 3: `create_indicator_views.py` wrapped with a 5-minute hard timeout.
- Step 4: `refresh_latest_daily_indicators.py` wrapped with a 30-minute hard timeout.
- Step 5: `refresh_polygon_vti_enrichment.py --stale-hours ${ENRICHMENT_STALE_HOURS:-24} --workers ${ENRICHMENT_WORKERS:-4} --rate-limit ${ENRICHMENT_RATE_LIMIT:-8} --news-limit ${ENRICHMENT_NEWS_LIMIT:-10}` wrapped with a 2-hour hard timeout. The old 8-worker / 20 req/s setting was removed because it overran Polygon server-side throttling despite paid-plan unlimited calls.
- Step 6: `build_vti_daily_enriched.py` wrapped with a 30-minute hard timeout.
- Step 7: `audit_coverage.py --strict` wrapped with a 5-minute hard timeout.

## Fixes shipped

### `daily_append.py`

Fixed grouped daily fast-path validation. Polygon aggregate endpoints can return valid payloads without the standard `status='OK'` field. The grouped fast path now uses `PolygonClient._success(resp, '/v2/aggs/grouped')` instead of `_ok(resp)`, preventing valid grouped responses from being falsely treated as empty and sent to the slow per-ticker fallback.

### `refresh_latest_daily_indicators.py`

Hardened the fast latest-daily technical indicator updater:

- Detects whether `technical_indicators` exists before querying it, so first-run/temp-DB smoke tests do not fail.
- Emits the canonical 32-column `technical_indicators` schema even when no tickers need refresh.
- Keeps explicit-column `INSERT` semantics instead of unsafe `SELECT *`.
- Leaves Bollinger/MACD/VWAP/POC fields null intentionally; this updater exists to refresh latest recursive daily RSI/ATR/Keltner rows quickly.

### `audit_coverage.py`

Converted the read-only audit from print-only reporting into a cron gate:

- Default mode remains read-only report, exit 0.
- `--strict` exits non-zero on missing/empty required tables, insufficient VTI coverage, stale daily bars, stale enriched snapshots, or daily technical indicators lagging daily bars.
- Strict thresholds are structural, not alpha-opinionated: optional fields such as dividends, splits, keyword-factor membership, and short interest are reported but do not fail cron.

### `daily_warehouse_refresh.sh`

Deleted the stale inline Python coverage block and replaced it with:

```bash
timeout --signal=KILL ${TIMEOUT_STEP7} /usr/bin/python3 /home/nima/market-data/audit_coverage.py --strict
```

Manual audit and cron audit now use the same code path.

### `daily_warehouse_refresh_retry.sh`

Added a production retry wrapper for cron job `5800f96cc1f7`. It prevents overlapping DuckDB writers with `flock`, logs to `/home/nima/market-data/logs/daily_warehouse_refresh_<run_id>.log`, retries the full idempotent pipeline up to 3 times after any non-zero exit, merges partial staged outputs before retrying, and progressively de-risks Polygon enrichment after failures (`4→3→2` workers, `8→5→3` req/s, `10→10→5` news limit).

### `test_pipeline.py`

Strengthened smoke testing:

- No-API mode creates a temp DuckDB database, inserts 60 fixture bars per ticker, creates a minimal indicator view, and verifies `refresh_latest_daily_indicators.compute_latest()` plus `upsert()`.
- Live API mode now prefers liquid US tickers (`AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, etc.) so the grouped endpoint probe validates real Polygon grouped data instead of foreign/OTC-style holdings symbols.
- Production DB remains untouched in both modes.

## Verification actually run

Commands and results:

- `/usr/bin/python3 -m py_compile ...`: passed for `daily_append.py`, `refresh_latest_daily_indicators.py`, `audit_coverage.py`, `test_pipeline.py`, `pull_ticker_details.py`, `refresh_polygon_vti_enrichment.py`, and `build_vti_daily_enriched.py`.
- `bash -n ~/.hermes/scripts/daily_warehouse_refresh.sh` and `bash -n ~/.hermes/scripts/daily_warehouse_refresh_retry.sh`: passed.
- `/usr/bin/python3 ~/market-data/daily_append.py --dry-run`: passed; target date 2026-05-29, grouped endpoint primary, fallback only if grouped returns zero.
- `/usr/bin/python3 ~/market-data/refresh_latest_daily_indicators.py --dry-run --tickers AAPL MSFT NVDA`: passed; computed 0 rows because production daily indicators were already current for those tickers.
- `/usr/bin/python3 ~/market-data/test_pipeline.py`: passed; temp DB only, 300 fixture rows, 5 latest daily indicator rows upserted, production DB untouched.
- `/usr/bin/python3 ~/market-data/test_pipeline.py --live-api --tickers 5`: passed; Polygon grouped endpoint returned 5 bars from 12,272 grouped results for 2026-05-29; fixture lookback then verified the indicator updater.
- `/usr/bin/python3 ~/market-data/refresh_polygon_vti_enrichment.py --classify-only`: passed; keyword factor baskets rebuilt without external API calls.
- `/usr/bin/python3 ~/market-data/build_vti_daily_enriched.py --dry-run`: passed; 3,319 rows, 3,293 with price, 3,245 with ticker details, 3,229 with Polygon keywords, max price/recursive indicator dates 2026-05-29.
- `/usr/bin/python3 ~/market-data/pull_ticker_details.py --insert-only`: passed; no stale `ticker_details.ndjson` pending.
- Production cron `5800f96cc1f7` run through `daily_warehouse_refresh_retry.sh`: passed on attempt 1/3 at 2026-05-30T08:08:31-04:00.
- `/usr/bin/python3 ~/market-data/audit_coverage.py --strict`: passed after the cron run.

## Current warehouse audit snapshot

Observed by `audit_coverage.py --strict` on 2026-05-30:

- VTI universe: 3,353 tickers.
- `daily_bars`: 3,304 / 3,353 VTI tickers, 98.5%, 3,750,619 rows, max date 2026-05-29.
- `ticker_details`: 3,246 / 3,353 VTI tickers, 96.8%, 3,246 rows.
- `technical_indicators`: 3,302 / 3,353 VTI tickers, 98.5%, 46,445,779 rows; daily max date 2026-05-29.
- `polygon_ticker_enrichment_latest`: 3,314 / 3,353 VTI tickers, 98.8%, 3,319 rows, max enriched date 2026-05-30.
- `vti_daily_enriched_latest`: 3,314 / 3,353 VTI tickers, 98.8%, 3,319 rows; as_of_date 2026-05-30.
- Latest `vti_daily_enriched_runs`: row_count 3,319; with_price 3,293; with_ticker_details 3,245; with_yfinance_sector 3,259; with_polygon_keywords 3,188; with_calc_indicators 3,291; max price/recursive/calc indicator dates all 2026-05-29.

## Operational runbook

For manual refreshes, do not run the whole pipeline as a foreground terminal command. Use one of:

```bash
# Preferred for agent-triggered bounded refreshes
terminal background=true notify_on_complete=true command="/usr/bin/bash /home/nima/.hermes/scripts/daily_warehouse_refresh.sh"

# Or schedule/run via Hermes cron so output returns to the origin channel
cronjob run <daily-refresh-job-id>
```

For smoke testing without long external pulls:

```bash
/usr/bin/python3 /home/nima/market-data/test_pipeline.py
/usr/bin/python3 /home/nima/market-data/test_pipeline.py --live-api --tickers 5
/usr/bin/python3 /home/nima/market-data/audit_coverage.py --strict
```

Use `SKIP_TICKER_DETAILS=1` only for manual smoke runs of the shell pipeline; production cron should keep ticker details enabled but default to `DETAILS_STALE_DAYS=30`.

## Stale entries cleaned

- Deleted the stale inline coverage/audit Python block from `daily_warehouse_refresh.sh`.
- Replaced stale verification numbers in this doc and the local market-data reference copy with the 2026-05-30 audit output.
- Updated project index and project manifest to reflect the strict audit gate and current verification state.
- Deleted stale 7-day ticker-details cadence from the default daily cron path; the current default is 30 days with explicit override support.
- Deleted stale 8-worker / 20 req/s Polygon enrichment default from the daily cron path; the current default is 4 workers / 8 req/s / 10 news articles with retry backoff.
