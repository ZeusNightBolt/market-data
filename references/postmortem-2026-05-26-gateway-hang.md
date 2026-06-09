# Postmortem: 42-Hour Gateway Hang — May 24–26, 2026

> **Status**: Root cause identified. Shell + Python fixes deployed. Swap expansion pending manual sudo.

---

## What Happened

On **May 24, 2026 at 16:46 ET**, a Codex (gpt-5.5) session spawned the daily warehouse
refresh pipeline. A terminal command ran for **152,111 seconds (≈42 hours)** before
returning on May 26 at 11:01 ET. The agent session was completely blocked during
this period. Simultaneous DNS failures for `api.telegram.org` made the gateway
unreachable via Telegram even though the gateway process never crashed (PID 616131
ran continuously).

**User impact**: No Telegram communication possible for ~18 hours (May 26 morning +
prior evening). Gateway appeared "down" but was actually alive and stuck.

---

## Root Cause Analysis

### The pipeline is NOT pulling "60 hours of data"

`daily_append.py` correctly targets **one calendar date** (defaults to yesterday).
The 42-hour hang was caused by a timeout cascade, not by pulling too much data.

### Timeout cascade (bottom-up)

| Layer | Mechanism | What Failed |
|-------|-----------|-------------|
| **HTTP** | `urllib.request.urlopen(timeout=30)` | Socket timeout, not wall-clock. Polygon server can establish TCP then trickle bytes every 29s — timeout never fires. Each `custom_bars()` call hangs indefinitely. |
| **Thread pool** | `as_completed(futures, timeout=600)` | Waits 600s for ANY future to complete. If all 4 workers are hung on trickle responses, no future completes → `TimeoutError` raised. |
| **Error handling** | Uncaught `TimeoutError` | The `TimeoutError` from `as_completed` was **not caught**. Script crashed, leaving the shell runner stalled with no signal. |
| **Shell** | No `timeout` wrapper | Each pipeline step had unlimited runtime. A hung Python process was never killed. |

### Why 42 hours specifically?

The pipeline was run as a **foreground terminal command** from the agent session.
The agent turn loop blocks until the tool returns. With no shell-level timeout:
- Python script hung on Polygon API trickle responses
- Agent sat waiting for the terminal tool to return
- Terminal tool waited forever because no wall-clock timeout enforced

### Contributing factors

- **DNS outage** (May 26 11:01): `api.telegram.org` resolution failed (`Name or service not known`). Gateway fell back to hardcoded IP `149.154.166.110` but Telegram was intermittently unreachable.
- **Zombie Firefox** (since May 22): Consumed 460 MB RAM, pushed system into swap (3.7/4 GB used). Killed during postmortem cleanup.

---

## Fixes Deployed

### 1. Shell: `timeout --signal=KILL` per step (`daily_warehouse_refresh.sh`)

```bash
TIMEOUT_STEP1=2700   # daily bars: 45 min (grouped=seconds, fallback ≤22 min)
TIMEOUT_STEP2=1800   # ticker details: 30 min
TIMEOUT_STEP3=300    # SQL views: 5 min
TIMEOUT_STEP4=1800   # indicators: 30 min
TIMEOUT_STEP5=7200   # Polygon enrichment: 2 hr (rate-limited, 3,353×2 calls)
TIMEOUT_STEP6=1800   # VTI enriched: 30 min
TIMEOUT_STEP7=300    # coverage: 5 min

timeout --signal=KILL ${TIMEOUT_STEP1} /usr/bin/python3 daily_append.py
# ... (all 7 steps wrapped)
```

`--signal=KILL` means the **kernel** terminates the process — no Python I/O trick can
defeat it. Total max pipeline runtime: ~4.5 hours (was: unlimited).

### 2. Python: Graceful `as_completed` timeout (`daily_append.py`)

```python
# BEFORE: uncaught TimeoutError crashes script
for f in as_completed(futures, timeout=600):
    f.result(timeout=60)

# AFTER: caught, partial results collected, incomplete tickers logged
try:
    for f in as_completed(futures, timeout=600):
        processed.add(futures[f])
        f.result(timeout=60)
except TimeoutError:
    remaining = len(tickers) - len(processed)
    print(f"⚠ timeout — {len(processed):,}/{len(tickers):,} collected, {remaining:,} remaining")
    # Cancel hung futures, log incomplete tickers
```

The function now returns `(succeeded, failed, remaining)` so callers know when data
is incomplete. Partial NDJSON is still valid — DuckDB merge uses `WHERE NOT EXISTS`
so subsequent runs fill gaps.

### 3. Memory: Killed zombie Firefox (PID 427519, since May 22)

Freed 1.1 GB RAM + 2 GB swap. System went from 3.7/4 GB swap to 1.6/4 GB.

---

## Prevention Checklist for Future Pipeline Work

- [ ] **Never run long pipelines as foreground terminal commands from an agent session.** Use cron jobs (`cronjob create`) or `terminal(background=true, notify_on_complete=true)`.
- [ ] **Every shell script step gets a `timeout` wrapper.** If a step can hang, it will hang.
- [ ] **`urllib` socket timeouts are NOT wall-clock timeouts.** For HTTP calls that must terminate, use `signal.alarm()` / `threading.Timer` / `requests` with `(connect, read)` tuple, or rely on the shell `timeout --signal=KILL` as the hard backstop.
- [ ] **`as_completed()` timeout handlers must catch `TimeoutError`.** Uncaught = crash = pipeline stall.
- [ ] **Monitor `state.db` size.** Grew from 82 MB (May 11) to 201 MB (May 26). Not urgent but worth investigating.
- [ ] **Swap expansion**: 4 GB → 8 GB recommended for CHUWI LarkBox X (5.7 GB RAM). Requires manual sudo.

---

## Files Changed

| File | Change |
|------|--------|
| `~/.hermes/scripts/daily_warehouse_refresh.sh` | Added `timeout --signal=KILL` wrappers on all 7 steps |
| `~/market-data/daily_append.py` | `pull_fallback_parallel()` catches `TimeoutError`, collects partial results, returns `(ok, fail, remaining)` |

## Swap Expansion (Pending)

Blocked by agent `sudo` restriction. Run manually:

```bash
sudo swapoff /swapfile && sudo rm /swapfile && \
sudo dd if=/dev/zero of=/swapfile bs=1M count=8192 status=progress && \
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

---

*Generated: 2026-05-26 — Hermes agent session `20260526_143344_0907a5bc`*
