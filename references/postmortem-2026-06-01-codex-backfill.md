# Postmortem: Hourly Backfill Crash — Jun 1, 2026

**Severity:** P1 — computer hung, data not merged, 3 failed attempts in 10 minutes
**Date:** 2026-06-01, 16:18–16:27 ET
**Root cause:** Subagent reimplemented `pull_hourly.py` from scratch, ignoring 56 documented pitfalls

---

## Timeline

| Time | Event | Detail |
|---|---|---|
| 16:18:23 | Run #1 starts | `backfill_hourly_recent.py` launched with 8 workers, zero throttle, 3,174 tickers |
| 16:18:24–16:18:47 | 0% success | 1,000 attempts, 0 succeeded. Polygon server dropped all requests. Computer hung — 8 workers blocked indefinitely on un-responded API calls. |
| 16:21:25 | Run #2 starts | Same script, different concurrency (workers reduced?). Pull succeeded: 3,172/3,174 succeeded, 250,435 rows in-memory. |
| 16:24:11 | Run #2 crashes | `db.register("__backfill_rows", batch_rows)` — DuckDB `InvalidInputException`: Python `list` is not a valid replacement scan type. |
| 16:24:37 | Run #3 starts | Same script, attempted fix for merge. Pull succeeded again: 3,172/3,174 succeeded, 250,452 rows. |
| 16:26:56 | Run #3 crashes | `SELECT changes()` — DuckDB `CatalogException`: function `changes` does not exist (SQLite-ism). |
| 16:27 | Debug begins | Investigation identifies 5 pitfall violations. `backfill_hourly_recent.py` deleted. `pull_hourly.py` patched with `RECENT_GAP_TICKERS` detection. |

## Root Cause

A subagent (Codex/gpt-5.5) was asked to backfill recent hourly data. Instead of reading the existing `pull_hourly.py` (511 lines, production-tested, year-chunked, 6 workers, NDJSON staging), it created `backfill_hourly_recent.py` — a 141-line from-scratch reimplementation that violated **five** documented pitfalls from the polygon-data-warehouse skill:

| Pitfall | Rule | What the script did |
|---|---|---|
| **#40** | Max 2–3 workers for heavy range scans + 1.5s inter-call gaps | **8 workers, zero throttle** → Polygon dropped 100% of requests in run #1 |
| **#39** | Stream to NDJSON staging file, never accumulate rows in memory | **250K rows in a Python list** (`batch_rows`) |
| **#22** | DuckDB merge via `read_json()` on NDJSON file | `db.register()` with a raw Python list (DuckDB requires DataFrame/PyArrow) |
| **#20** | DuckDB SQL, not SQLite SQL | `SELECT changes()` — exists in SQLite, not in DuckDB |
| **#64** | Agent-invoked terminal blocks session on long tasks | Script ran directly from agent terminal with no timeout/escape path |

### Why the computer hung

Run #1 sent 8 concurrent Polygon API calls for **12-day range scans** — these are heavier than the 1-year chunks `pull_hourly.py` uses. Polygon's server-side query queue rejected all of them. The 8 worker threads sat waiting for responses that never came, while the parent process held the agent's terminal session. With no `ThreadPoolExecutor` timeout and Pitfall #64 (agent terminal blocks indefinitely), the session froze.

The correct approach, already implemented in `pull_hourly.py`:
- Split 5-year ranges into 1-year chunks (2–5s each vs 10–30s)
- 6 workers safe for 1-year chunks (Polygon can handle these concurrently)
- Streaming NDJSON writer thread — zero memory accumulation
- `read_json('hourly.ndjson', format='newline_delimited')` — tested DuckDB merge

## What Was Fixed

1. **Deleted `backfill_hourly_recent.py`** — the broken reimplementation
2. **Extended `pull_hourly.py` with recent-gap detection** (`RECENT_GAP_TICKERS`, lines 38, 134–170, 189–199):
   - Queries DuckDB for tickers where `daily MAX(ts) > hourly MAX(ts) + 3 days`
   - `generate_chunks()` now returns only the gap range for these tickers instead of full 2021–present
   - Dry run confirms: 2 recent-gap tickers (was 3,174 in the broken script)
3. **Prevention rule** — Pitfall #65 added to polygon-data-warehouse skill: "Never reimplement pull scripts from scratch"

## Data Impact

- **No data was lost.** The 250K rows pulled in runs #2 and #3 were never merged into DuckDB (both crashed at the insert stage). The correct pipeline will detect and re-pull only the actual gaps.
- **No corruption.** DuckDB was never written to by any of the three runs.

## Prevention Rules

1. **Never reimplement a pull pipeline from scratch** — always extend the existing one. If a feature is missing (like recent-gap detection), add it to the canonical script.
2. **Use cron for scheduled pulls, not agent terminals** — Pitfall #64 is fatal for long-running tasks.
3. **Read the skill's pitfalls before writing code** — all five violations were already catalogued.
4. **Subagents don't inherit skill context** — when delegating data pipeline work, include the skill's pitfall list in the handoff context, or the subagent will hallucinate a solution from thin air.
5. **Run `--dry-run` before any pull** — it would have shown 3,174 tickers vs the expected ~70 and prevented the 0% success run.

## Verification

```
$ python3 pull_hourly.py --dry-run
Recent gap tickers (daily > hourly + 3d): 2
VTI: 3,353 | Complete: 3,281 | Incomplete: 72
Est. time: ~5 min at 6 workers
```

The pipeline is ready. The 72 incomplete tickers are mostly foreign/ADR tickers with zero bars in DuckDB — not a gap in US equity coverage. The 2 recent-gap tickers will be pulled in the next cron run.

---

*Postmortem filed: 2026-06-03. Last verified: pull_hourly.py --dry-run normal.*
