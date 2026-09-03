# P04 Validation - collect

Date: 2026-09-02

Scope: `strategy-lab collect` only. Covered requirements: R-COL-1..13, preserving
R-ISO-1..6 and invariants I-1..I-14.

## What changed

- Added injectable UTC clock and NTP preflight.
- Switched collection credentials to OS `keyring`, with env fallback only when
  `STRATEGY_LAB_ENV=vps` and variables are `IQ_EMAIL` / `IQ_PASSWORD`.
- Added canary fixture and canary validation before repository writes.
- Added `Repository` protocol and `FakeRepository` for P04; the real Supabase
  repository remains P05.
- Added idempotent M1 backfill by watermark, with closed-candle cutoff and batch
  size limited to 1000.
- Added market session calendar and gap classification.
- Added hourly payout sampling with incremental average.
- Added post-run invariants for monotonic timestamps, duplicates and jumps above
  `8 * ATR(14)`.
- Added collect runner and CLI commands:
  - `strategy-lab collect --dry-run`
  - `strategy-lab status --dry-run`

## Validation

Commands executed from the isolated `strategy-lab` environment:

```text
.venv\Scripts\python.exe -m pytest -q
254 passed in 49.83s

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
80 files already formatted

.venv\Scripts\python.exe -m mypy
Success: no issues found in 46 source files

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
status=never_run, last_run_stale=true, unresolved_in_session_gaps=0
```

`git diff --check -- strategy-lab` produced no whitespace errors, but the whole
`strategy-lab/` directory is still untracked in the parent repository. The line
diff is therefore not available until the user chooses to add the subproject to
Git.

## Tests added

- Canary fixture match and mismatch abort before write.
- Backfill idempotency across three runs.
- Current candle exclusion with UTC epoch logic.
- Invalid candle batch aborting with zero writes.
- Gap classification by market session.
- Payout sample count only for sampled hours.
- Suspect jump invariant.
- Dry-run collect JSON report.
- Status JSON report.
- Secret scrub on CLI failure.
- AST guard for `time.time()` and naive `datetime.now()` in collect modules.
- Credential routing through keyring or explicit VPS env only.

## Limitations

- No Supabase write path was implemented in P04. This is expected: P05 owns the
  Postgres/Supabase repository.
- No external IQ Option connection, credentials or order path was used.
- No real fixture was collected in this phase.
- Coverage percentage was not measured because adding a coverage dependency was
  not authorized by P04. The requested behavior is covered by focused tests, and
  the full suite is green.
