# Market-data untracked/dead-code cleanup — 2026-06-25

## Trigger

User requested documentation and cleanup of untracked files, hardcoded local paths, archives/dead code, and a Polygon client re-audit before adding live final-candidate price calls to Equity Screener.

## Untracked files found

| Path | Classification | Action |
|---|---|---|
| `refresh_corporate_actions_monthly.py` | Production monthly corporate-actions refresh script | Commit to repo; changed `ROOT` from `/home/nima/market-data` to `Path(__file__).resolve().parent`. |
| `test_refresh_corporate_actions_monthly.py` | Regression tests for the monthly corporate-actions refresh | Commit to repo. |
| `scripts/monthly_corporate_actions_refresh.sh` | Production no-agent cron wrapper target for `~/.hermes/scripts/monthly_corporate_actions_refresh.sh` | Commit to repo; made path repo-relative via `SCRIPT_DIR`/`REPO_DIR`. |
| `scripts/quick_refresh.sh` | Production lightweight refresh used by Equity Screener before dashboard build | Commit to repo; made DB/log/script paths repo-relative via `SCRIPT_DIR`/`REPO_DIR`. |

## Dead-code archive found

Tracked archive directory:

- `_archive/dead_code_20260609/batch_indicators.py`
- `_archive/dead_code_20260609/bulk_insert.py`
- `_archive/dead_code_20260609/chart_aapl_4h.py`
- `_archive/dead_code_20260609/market_data.duckdb?access_mode=READ_ONLY`
- `_archive/dead_code_20260609/pull_daily.py`
- `_archive/dead_code_20260609/pull_historical.py`
- `_archive/dead_code_20260609/pull_hourly.py.v4.bak`

Ignored local binary residue:

- `_archive/dead_code_20260609/market_data.db`

Action: remove `_archive/dead_code_20260609/` from the repo and local filesystem. The canonical current paths are `pull_hourly.py`, `daily_append.py`, `build_higher_timeframes.py`, `refresh_latest_*_indicators.py`, and `audit_coverage.py`. Historical rationale remains in `references/postmortem-2026-06-01-codex-backfill.md` and this cleanup note; no runnable legacy code should remain in the production tree.

## Polygon client re-audit outcome

Verified client facts:

- `polygon_client.py` already has retry/backoff, rate limiting, endpoint-aware aggregate success checks, and correct aggregate pagination using full `next_url` paths.
- Snapshot endpoint method exists but only returned raw response before this cleanup.
- Snapshot response shape is non-standard: `{status, ticker: {lastTrade, min, day, prevDay}}`, not `{results: [...]}`.

Added:

- `PolygonClient.latest_price(ticker)` — extracts freshest available price from snapshot in order: `lastTrade.p`, `min.c`, `day.c`, `prevDay.c`.
- `PolygonClient.latest_prices(tickers)` — side-effect-free parallel helper for final candidate price overlays.
- `polygon_latest_prices.py` — CLI JSON tool for ad-hoc latest price calls without DuckDB writes.
- `test_polygon_latest_price.py` — regression tests for extraction order and per-ticker bulk output.

## Equity Screener integration

Equity Screener now selects final dashboard candidate tickers after deterministic scoring, calls Polygon snapshot prices only for that final set, and overlays `display_close`/`price_source` for those candidates. Rankings remain warehouse-derived; final displayed prices become as fresh as Polygon snapshot permits at build time.

New payload fields:

- `warehouse_display_close`
- `latest_polygon_price`
- `latest_polygon_price_source`
- `latest_polygon_price_timestamp`
- `latest_polygon_price_status`

## Verification commands

```bash
cd /home/nima/market-data
/usr/bin/python3 -m unittest -v test_polygon_latest_price.py test_refresh_corporate_actions_monthly.py test_audit_coverage.py
/usr/bin/python3 -m py_compile polygon_client.py polygon_latest_prices.py refresh_corporate_actions_monthly.py
bash -n scripts/quick_refresh.sh scripts/monthly_corporate_actions_refresh.sh
/usr/bin/python3 audit_coverage.py --strict

cd /home/nima/equity-screener
/usr/bin/python3 -m unittest -v test_build_dashboard.py
/usr/bin/python3 scripts/build_dashboard.py --price-filter 75 --no-llm
```
