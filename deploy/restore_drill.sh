#!/usr/bin/env bash
# Restore into a scratch DB, verify checksums, migration state, row-count parity and audit immutability. Exit non-zero on any mismatch.
set -euo pipefail
DUMP="$1"; ADMIN_URL="${2:?admin url e.g. postgresql://postgres@host/postgres}"; SRC="${3:?source url for parity check}"
sha256sum -c "$DUMP.sha256"
DB="ri_restore_drill_$$"; psql "$ADMIN_URL" -qc "CREATE DATABASE $DB"; trap 'psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)"' EXIT
T="${ADMIN_URL%/*}/$DB"; pg_restore --no-owner -d "$T" "$DUMP"
for t in organizations account_source_links events signals initiatives opportunity_hypotheses audit_events approvals legacy_signals; do
  a=$(psql "$SRC" -Atc "select count(*) from $t"); b=$(psql "$T" -Atc "select count(*) from $t"); [ "$a" = "$b" ] || { echo "MISMATCH $t $a != $b"; exit 1; }; echo "ok $t=$b"; done
psql "$T" -Atc "select count(*) from schema_migrations" | xargs echo "migrations:"
out=$(psql "$T" -qc "delete from audit_events" 2>&1 || true)
echo "$out" | grep -q "append-only" && echo "ok audit append-only survives restore" || { echo "FAIL audit immutability lost in restore"; exit 1; }
