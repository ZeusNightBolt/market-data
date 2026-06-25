#!/usr/bin/bash
set -euo pipefail
export PYTHONUNBUFFERED=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_DIR"

# Schedule this script on days 28-31. Stay silent unless today is the final
# calendar day of the month, so no_agent cron does not spam on non-EOM days.
if [[ "$(date -d tomorrow +%m)" == "$(date +%m)" && "${FORCE_CORPORATE_ACTIONS_REFRESH:-0}" != "1" ]]; then
  exit 0
fi

exec 9>/tmp/monthly_corporate_actions_refresh.lock
if ! flock -n 9; then
  echo "Another corporate actions refresh is already running. Exiting without overlap."
  exit 75
fi

LOG_DIR="${REPO_DIR}/logs"
mkdir -p "$LOG_DIR"
RUN_ID="$(date +%Y%m%dT%H%M%S%z)"
LOG="$LOG_DIR/corporate_actions_refresh_${RUN_ID}.log"
LATEST="$LOG_DIR/corporate_actions_refresh_latest.log"

{
  echo "# Monthly corporate actions refresh"
  echo "run_id=${RUN_ID}"
  echo "started_at=$(date -Is)"
  timeout --signal=KILL "${TIMEOUT_CORPORATE_ACTIONS:-7200}" /usr/bin/python3 "${REPO_DIR}/refresh_corporate_actions_monthly.py" --workers "${CORPORATE_ACTIONS_WORKERS:-4}"
  echo "finished_at=$(date -Is)"
} 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
ln -sfn "$LOG" "$LATEST"
exit "$status"
