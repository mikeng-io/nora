#!/usr/bin/env bash
set -euo pipefail

# Alerts if the SSE listener has been silent for more than 300 seconds.
# Intended to run from cron or a systemd timer, e.g. every 60 seconds.

THRESHOLD=300
HEALTH_URL="${NORA_HEALTH_URL:-http://127.0.0.1:9100/health}"

body="$(curl -sS --max-time 5 "$HEALTH_URL")" || {
    echo "ALERT: /health unreachable" >&2
    exit 1
}

age="$(echo "$body" | python3 -c "import json,sys; print(json.load(sys.stdin)['sse']['last_event_age_seconds'])" 2>/dev/null || echo "")"

if [ -z "$age" ] || [ "$age" = "None" ]; then
    exit 0    # no events yet — fresh, not silent
fi

age_int="${age%%.*}"
if [ "$age_int" -gt "$THRESHOLD" ]; then
    echo "ALERT: SSE silent for ${age_int}s (> ${THRESHOLD}s threshold)" >&2
    exit 1
fi

exit 0
