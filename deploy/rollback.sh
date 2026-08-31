#!/usr/bin/env bash
set -Eeuo pipefail
APP_ROOT="${APP_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PREVIOUS="${PREVIOUS:-previous}"
LOG_DIR="${LOG_DIR:-$APP_ROOT/var/log}"
mkdir -p "$LOG_DIR"
printf '%s rollback reason=%s previous=%s\n' "$(date -u +%FT%TZ)" "${REASON:-operator_requested}" "$PREVIOUS" >> "$LOG_DIR/rollback.log"
test -d "$APP_ROOT/releases/$PREVIOUS" || { echo "previous release missing" >&2; exit 2; }
ln -sfn "$APP_ROOT/releases/$PREVIOUS" "$APP_ROOT/releases/active"
echo "rollback complete; logs preserved"
