#!/usr/bin/bash
# Unified warehouse quick refresh — pulls latest hourly data + rebuilds
# indicators.  Designed to run BEFORE the equity screener (20:30 ET) to
# ensure fresh 1h/4h data points are available.
#
# This replaces the old split-cron approach:
#   OLD: hourly cron (9-16 ET) + daily warehouse (9:30 ET)
#   NEW: daily warehouse (9:30 ET) + this quick refresh (before screener)
#
# The daily warehouse refresh at 9:30 ET runs the FULL pipeline including
# ticker details, enrichment, factor baskets — that's the heavy lift.
# This script is LIGHTWEIGHT: just the hourly pull + indicator rebuild,
# designed to run in ~5 minutes.

set -euo pipefail
export PYTHONUNBUFFERED=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="${REPO_DIR:-$DEFAULT_REPO_DIR}"
DB="${DB:-${REPO_DIR}/market_data.duckdb}"
export DB
LOG_DIR="${LOG_DIR:-${REPO_DIR}/logs}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
TIMEOUT_BIN="${TIMEOUT_BIN:-timeout}"
LOCK_FILE="${LOCK_FILE:-/tmp/market_data_quick_refresh.lock}"
PULL_HOURLY_TIMEOUT_SECONDS="${PULL_HOURLY_TIMEOUT_SECONDS:-600}"
BUILD_HIGHER_TIMEOUT_SECONDS="${BUILD_HIGHER_TIMEOUT_SECONDS:-300}"
CREATE_VIEWS_TIMEOUT_SECONDS="${CREATE_VIEWS_TIMEOUT_SECONDS:-300}"
REFRESH_INTRADAY_TIMEOUT_SECONDS="${REFRESH_INTRADAY_TIMEOUT_SECONDS:-600}"
REFRESH_DAILY_TIMEOUT_SECONDS="${REFRESH_DAILY_TIMEOUT_SECONDS:-300}"
# Market-aware freshness gate.  Sunday night legitimately sees Friday-close
# hourly/4h data.  Weekdays must be strict; weekends allow closed-market age.
WEEKDAY_MAX_AGE_HOURS="${WEEKDAY_MAX_AGE_HOURS:-30}"
WEEKEND_MAX_AGE_HOURS="${WEEKEND_MAX_AGE_HOURS:-80}"

mkdir -p "$LOG_DIR"
RUN_ID="$(date +%Y%m%dT%H%M%S%z)"
LOG_FILE="$LOG_DIR/quick_refresh_${RUN_ID}.log"

exec > >(tee -a "$LOG_FILE") 2>&1

fail() {
    local rc="$1"
    shift
    echo "   ❌ ERROR: $*"
    echo ""
    echo "═══════════════════════════════════════"
    echo "  QUICK REFRESH FAILED"
    echo "  $(date -Is)"
    echo "  Exit: $rc"
    echo "  Log: $LOG_FILE"
    echo "═══════════════════════════════════════"
    exit "$rc"
}

run_step() {
    local label="$1"
    local timeout_seconds="$2"
    local rc
    shift 2

    echo ""
    echo "── ${label} ──"
    if "$TIMEOUT_BIN" --signal=KILL "$timeout_seconds" "$@"; then
        echo "   ✅ ${label} complete"
        return 0
    else
        rc=$?
        fail "$rc" "${label} failed; fail-closed instead of continuing with stale/partial warehouse data"
    fi
}

verify_freshness() {
    "$PYTHON_BIN" - "$DB" "$WEEKDAY_MAX_AGE_HOURS" "$WEEKEND_MAX_AGE_HOURS" <<'PY'
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import duckdb

DB_PATH = sys.argv[1]
WEEKDAY_MAX = float(sys.argv[2])
WEEKEND_MAX = float(sys.argv[3])

now_utc = dt.datetime.now(dt.timezone.utc)
now_market = now_utc.astimezone(ZoneInfo("America/New_York"))
threshold = WEEKEND_MAX if now_market.weekday() in (5, 6) else WEEKDAY_MAX
checks = [
    ("hourly", "hourly_bars", ""),
    ("daily", "daily_bars", ""),
    ("indicators", "technical_indicators", ""),
    ("4h indicators", "technical_indicators", "WHERE timeframe='4h' AND close IS NOT NULL"),
]
failures = []
with duckdb.connect(DB_PATH, read_only=True) as db:
    for label, table, where_clause in checks:
        row = db.execute(
            f"SELECT max(to_timestamp(CAST(timestamp/1000 AS BIGINT))) FROM {table} {where_clause}"
        ).fetchone()
        latest = row[0] if row else None
        if latest is None:
            print(f"  ❌ {label:15s} NO DATA")
            failures.append(f"{label}: no data")
            continue
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=dt.timezone.utc)
        else:
            latest = latest.astimezone(dt.timezone.utc)
        age_hours = (now_utc - latest).total_seconds() / 3600
        ok = age_hours <= threshold
        status = "✅" if ok else "❌"
        print(
            f"  {status} {label:15s} max={latest.strftime('%Y-%m-%d %H:%M')} "
            f"({age_hours:.1f}h ago; threshold={threshold:.0f}h)"
        )
        if not ok:
            failures.append(f"{label}: {age_hours:.1f}h old > {threshold:.0f}h threshold")

if failures:
    print("FATAL freshness verification failed:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(20)
PY
}

# Prevent overlapping quick-refresh/manual runs against one DuckDB file and one
# shared raw/hourly.ndjson staging file.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    fail 75 "another market-data quick refresh is already running (lock: $LOCK_FILE)"
fi

echo "═══════════════════════════════════════"
echo "  WAREHOUSE QUICK REFRESH"
echo "  Run: $RUN_ID"
echo "  $(date -Is)"
echo "  Repo: $REPO_DIR"
echo "  DB: $DB"
echo "═══════════════════════════════════════"

run_step "Step 1: Pull latest hourly bars" "$PULL_HOURLY_TIMEOUT_SECONDS" "$PYTHON_BIN" "${REPO_DIR}/pull_hourly.py"
run_step "Step 2: Build higher timeframes" "$BUILD_HIGHER_TIMEOUT_SECONDS" "$PYTHON_BIN" "${REPO_DIR}/build_higher_timeframes.py"
run_step "Step 3: Recreate indicator views" "$CREATE_VIEWS_TIMEOUT_SECONDS" "$PYTHON_BIN" "${REPO_DIR}/create_indicator_views.py"
run_step "Step 4: Refresh latest intraday indicators (1h + 4h)" "$REFRESH_INTRADAY_TIMEOUT_SECONDS" "$PYTHON_BIN" "${REPO_DIR}/refresh_latest_intraday_indicators.py"
run_step "Step 5: Refresh latest daily indicators" "$REFRESH_DAILY_TIMEOUT_SECONDS" "$PYTHON_BIN" "${REPO_DIR}/refresh_latest_daily_indicators.py"

echo ""
echo "── Verification ──"
if verify_freshness; then
    echo "   ✅ Verification complete"
else
    rc=$?
    fail "$rc" "warehouse freshness verification failed"
fi

echo ""
echo "═══════════════════════════════════════"
echo "  QUICK REFRESH COMPLETE"
echo "  $(date -Is)"
echo "  Log: $LOG_FILE"
echo "═══════════════════════════════════════"
