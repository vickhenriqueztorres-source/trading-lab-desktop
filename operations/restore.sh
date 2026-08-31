#!/usr/bin/env bash
set -Eeuo pipefail
BACKUP="${BACKUP:?set BACKUP}"
TARGET_DIR="${TARGET_DIR:?set isolated TARGET_DIR}"
KEY_FILE="${KEY_FILE:?set KEY_FILE to an operator-managed secret file}"
test -f "$BACKUP" && test -f "$BACKUP.sha256" || { echo "backup/checksum missing" >&2; exit 2; }
test -f "$KEY_FILE" || { echo "encryption key file missing" >&2; exit 2; }
sha256sum --check "$BACKUP.sha256"
mkdir -p "$TARGET_DIR"
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"$KEY_FILE" -in "$BACKUP" | tar -xzf - -C "$TARGET_DIR"
echo "restore extracted; run integrity/WAL checks before promotion"
