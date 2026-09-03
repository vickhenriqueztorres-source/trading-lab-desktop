-- Migration 0006: holdout_burned table for sealed holdout management (R-RES-2)
create table if not exists public.holdout_burned (
  range_id text primary key,
  from_ts bigint not null,
  to_ts bigint not null,
  burned_at bigint not null,
  run_id text references public.research_runs(run_id),
  check (from_ts >= 0 and to_ts > from_ts),
  check (from_ts % 60 = 0 and to_ts % 60 = 0),
  check (burned_at >= 0)
);

alter table public.holdout_burned enable row level security;
revoke all on public.holdout_burned from anon, authenticated;
