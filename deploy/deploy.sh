#!/usr/bin/env bash
set -Eeuo pipefail
APP_ROOT="${APP_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
RELEASE="${RELEASE:-current}"
PREVIOUS="${PREVIOUS:-previous}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8765/health}"
echo "[deploy] EXPAND migration and stage non-financial canary"
mkdir -p "$APP_ROOT/releases/$RELEASE"
if ! curl --fail --silent --show-error --max-time 5 "$HEALTH_URL" >/dev/null; then
  REASON="canary_health_failed" "$APP_ROOT/deploy/rollback.sh"
  exit 1
fi
echo "release=$RELEASE previous=$PREVIOUS status=READY_FOR_PROMOTION"
