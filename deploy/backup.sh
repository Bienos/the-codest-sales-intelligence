#!/usr/bin/env bash
# Encrypted-at-rest is the storage layer's job; this produces a consistent custom-format dump + checksum.
set -euo pipefail
: "${DATABASE_URL:?}"; OUT="${1:-backups/ri-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname "$OUT")"; pg_dump --format=custom --no-owner "$DATABASE_URL" -f "$OUT"; sha256sum "$OUT" > "$OUT.sha256"; echo "$OUT"
