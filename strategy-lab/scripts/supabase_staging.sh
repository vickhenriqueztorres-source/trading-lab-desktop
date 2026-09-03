#!/usr/bin/env sh
set -eu

if [ -z "${SUPABASE_STAGING_DB_URL:-}" ]; then
  echo "SUPABASE_STAGING_DB_URL is required" >&2
  exit 1
fi

if [ -n "${SUPABASE_PROD_REF:-}" ] && printf '%s' "$SUPABASE_STAGING_DB_URL" | grep -q "$SUPABASE_PROD_REF"; then
  echo "Refusing to run migrations against production ref" >&2
  exit 1
fi

supabase_bin="${SUPABASE_CLI:-supabase}"

command -v "$supabase_bin" >/dev/null 2>&1 || {
  echo "Supabase CLI is required. Set SUPABASE_CLI to the local binary if needed." >&2
  exit 1
}

"$supabase_bin" db query \
  --workdir apps/hub \
  --db-url "$SUPABASE_STAGING_DB_URL" \
  "do \$\$ begin if not exists (select 1 from pg_available_extensions where name = 'pg_net') then raise exception 'PG_NET_UNAVAILABLE'; end if; end \$\$;" \
  >/dev/null || {
  echo "pg_net is not available. Use collect --archive as the fallback design before applying 0004." >&2
  exit 1
}

"$supabase_bin" db push --workdir apps/hub --db-url "$SUPABASE_STAGING_DB_URL" --skip-vault
