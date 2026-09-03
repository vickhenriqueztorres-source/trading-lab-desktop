# P05 Validation - Supabase schema and PostgresRepository

Date: 2026-09-02

Scope: Supabase migrations, RLS policy files, staging script, Postgres-backed
collect repository, and encrypted backup command. Covered requirements:
R-HUB-1, R-HUB-2, R-HUB-7, R-HUB-8 and R-OPS-1.

## What changed

- Added migrations under `apps/hub/supabase/migrations/`:
  - `0001_schema.sql`: tables, checks and indexes from Architecture section 3,
    plus `collect_runs` and `archive_jobs` needed by collect/archive operations.
  - `0002_rls.sql`: RLS enabled on all tables, anon denied by default, anon insert
    allowed only for `live_outcomes` matching JWT `client_id`.
  - `0003_sessions_seed.sql`: initial Forex and OTC `market_sessions` seed.
  - `0004_archive_cron.sql`: `pg_cron` + `pg_net` archive orchestration.
- Added `apps/hub/supabase/functions/archive/index.ts` as a stub for the archive
  Edge Function. It does not delete data.
- Added `scripts/supabase_staging.sh` to apply migrations only with
  `SUPABASE_STAGING_DB_URL`, refusing URLs containing `SUPABASE_PROD_REF`.
  It also prechecks `pg_net` availability before applying archive cron.
- Added `tools/strategy_lab/collect/pg_repository.py` with `PostgresRepository`.
- Added `strategy-lab backup`, using `pg_dump` and `age` through environment
  variables instead of command-line secrets.
- Added staging tests marked `@pytest.mark.staging`; they skip unless staging
  env vars are present and fail if the configured URL points to production.

## Validation executed locally

Supabase CLI installed locally, outside Git, from the official GitHub release:

```text
state/tools/supabase-cli-v2.116.0/supabase.exe
supabase --version = 2.116.0
archive checksum verified against checksums.txt
```

```text
.venv\Scripts\python.exe -m pytest -q
257 passed, 3 skipped in 45.46s

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
85 files already formatted

.venv\Scripts\python.exe -m mypy
Success: no issues found in 48 source files

.venv\Scripts\python.exe -m compileall -q tools packages
passed

.venv\Scripts\python.exe -m pip check
No broken requirements found.
```

CLI smoke:

```text
.venv\Scripts\strategy-lab.exe collect --dry-run
status=ok, asset=EURUSD-OTC, fetched=5, written=0, payout_return_ratio=0.87

.venv\Scripts\strategy-lab.exe status --dry-run
status=never_run, last_run_stale=true, backup_stale=true

.venv\Scripts\strategy-lab.exe backup
status=failed, with no secret output, because backup configuration/tools were absent
```

`git diff --check -- strategy-lab` passed.

## Staging not executed

Remote Supabase application was not executed in this environment:

- `psql` is not installed.
- `SUPABASE_STAGING_DB_URL` is not configured in the environment.
- The provided API keys are not a staging DB URL, and the prompt requires staging.
- `pg_net` availability was not proven against the remote project.

`scripts/supabase_staging.ps1` was added for Windows and stops before any remote
write when `SUPABASE_STAGING_DB_URL` is absent or when `SUPABASE_PROD_REF` appears
inside the target URL. `scripts/supabase_staging.sh` was updated to use Supabase
CLI instead of `psql`.

Because of that, these acceptance items remain blocked until staging credentials
and CLI/database tooling are available:

- migrations applied from zero in staging;
- staging RLS proof through the remote database/API;
- real `collect` with `--dry-run` disabled writing one asset to staging;
- second real collect proving zero new candles.

## Archive note

`pg_net` is async. The implemented migration schedules an archive request and
adds `complete_archive_job(job_id, archived_count)` as the only path that deletes
candles. Deletion occurs only after the archive side reports a matching count.
If `pg_net` is unavailable in staging, `scripts/supabase_staging.sh` stops before
applying `0004` and prints the fallback direction: implement `collect --archive`.
