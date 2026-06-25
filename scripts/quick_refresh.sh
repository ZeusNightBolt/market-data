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
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DB="${REPO_DIR}/market_data.duckdb"
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "$LOG_DIR"

RUN_ID="$(date +%Y%m%dT%H%M%S%z)"
LOG_FILE="$LOG_DIR/quick_refresh_${RUN_ID}.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "═══════════════════════════════════════"
echo "  WAREHOUSE QUICK REFRESH"
echo "  Run: $RUN_ID"
echo "  $(date -Is)"
echo "═══════════════════════════════════════"

# Step 1: Pull latest hourly bars (the ONLY API call)
echo ""
echo "── Step 1: Pull latest hourly bars ──"
if timeout --signal=KILL 600 /usr/bin/python3 "${REPO_DIR}/pull_hourly.py"; then
    echo "   ✅ Hourly pull complete"
else
    rc=$?
    echo "   ⚠️  Hourly pull failed (exit $rc) — continuing with existing data"
fi

# Step 2: Build higher timeframes from hourly (ZERO API)
echo ""
echo "── Step 2: Build higher timeframes ──"
if timeout --signal=KILL 300 /usr/bin/python3 "${REPO_DIR}/build_higher_timeframes.py"; then
    echo "   ✅ Higher timeframes built"
else
    rc=$?
    echo "   ⚠️  Higher timeframes failed (exit $rc)"
fi

# Step 3: Recreate indicator views
echo ""
echo "── Step 3: Recreate indicator views ──"
if timeout --signal=KILL 120 /usr/bin/python3 "${REPO_DIR}/create_indicator_views.py"; then
    echo "   ✅ Indicator views created"
else
    rc=$?
    echo "   ⚠️  Indicator views failed (exit $rc)"
fi

# Step 4: Refresh latest 1h/4h intraday indicators
echo ""
echo "── Step 4: Refresh latest intraday indicators (1h + 4h) ──"
if timeout --signal=KILL 600 /usr/bin/python3 "${REPO_DIR}/refresh_latest_intraday_indicators.py"; then
    echo "   ✅ Intraday indicators refreshed"
else
    rc=$?
    echo "   ⚠️  Intraday indicators failed (exit $rc)"
fi

# Step 5: Refresh latest daily indicators
echo ""
echo "── Step 5: Refresh latest daily indicators ──"
if timeout --signal=KILL 300 /usr/bin/python3 "${REPO_DIR}/refresh_latest_daily_indicators.py"; then
    echo "   ✅ Daily indicators refreshed"
else
    rc=$?
    echo "   ⚠️  Daily indicators failed (exit $rc)"
fi

# Verify data freshness
echo ""
echo "── Verification ──"
/usr/bin/python3 -c "
import duckdb, datetime
db = duckdb.connect('$DB', read_only=True)
for tbl, label in [('hourly_bars','hourly'), ('daily_bars','daily'), ('technical_indicators','indicators')]:
    r = db.execute(f\"SELECT max(timestamp)/1000 FROM {tbl}\").fetchone()
    if r[0]:
        dt = datetime.datetime.fromtimestamp(r[0], tz=datetime.timezone.utc)
        age_hr = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
        status = '✅' if age_hr < 24 else '⚠️'
        print(f'  {status} {label:15s} max={dt.strftime(\"%Y-%m-%d %H:%M\")} ({age_hr:.1f}h ago)')
    else:
        print(f'  ❌ {label:15s} NO DATA')
db.close()
"

echo ""
echo "═══════════════════════════════════════"
echo "  QUICK REFRESH COMPLETE"
echo "  $(date -Is)"
echo "  Log: $LOG_FILE"
echo "═══════════════════════════════════════"
