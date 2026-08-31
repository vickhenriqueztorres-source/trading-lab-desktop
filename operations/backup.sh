#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="${SOURCE_DIR:?set SOURCE_DIR to an approved snapshot}"
DEST_DIR="${DEST_DIR:?set DEST_DIR to encrypted storage}"
KEY_FILE="${KEY_FILE:?set KEY_FILE to an operator-managed secret file}"
case "$SOURCE_DIR" in *vault*|*broker_credentials*) echo "credential backup refused" >&2; exit 2;; esac
test -f "$KEY_FILE" || { echo "encryption key file missing" >&2; exit 2; }
mkdir -p "$DEST_DIR"
archive="$DEST_DIR/trading-lab-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
tar --exclude='*.token' --exclude='*.secret' --exclude='*.vault' -czf - -C "$SOURCE_DIR" . \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass file:"$KEY_FILE" -out "$archive.enc"
archive="$archive.enc"
sha256sum "$archive" > "$archive.sha256"
echo "backup created: $archive"
