-- P05 / R-HUB-1: Strategy Lab Supabase schema.
-- Epoch values are integer UTC seconds; money/probability values are numeric.

create table if not exists public.candles (
  asset text not null,
  ts bigint not null,
  o numeric(18,8) not null,
  h numeric(18,8) not null,
  l numeric(18,8) not null,
  c numeric(18,8) not null,
  tick_vol integer not null default 0,
  source text not null,
  collected_at bigint not null,
  primary key (asset, ts),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (ts >= 0 and ts % 60 = 0),
  check (tick_vol >= 0),
  check (l <= least(o,c) and greatest(o,c) <= h),
  check (collected_at >= 0)
);

create index if not exists candles_asset_ts_desc_idx on public.candles (asset, ts desc);

create table if not exists public.payouts (
  asset text not null,
  hour_ts bigint not null,
  payout_pct numeric(5,2),
  samples integer not null default 0,
  primary key (asset, hour_ts),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (hour_ts >= 0 and hour_ts % 3600 = 0),
  check (samples >= 0),
  check (payout_pct is null or (payout_pct >= 0 and payout_pct <= 100)),
  check ((samples = 0 and payout_pct is null) or (samples > 0 and payout_pct is not null))
);

create index if not exists payouts_asset_hour_ts_idx on public.payouts (asset, hour_ts);

create table if not exists public.market_sessions (
  asset text not null,
  weekday smallint not null,
  open_min smallint not null,
  close_min smallint not null,
  primary key (asset, weekday, open_min),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (weekday between 0 and 6),
  check (open_min >= 0 and open_min < 1440),
  check (close_min > open_min and close_min <= 1440)
);

create table if not exists public.gaps (
  asset text not null,
  from_ts bigint not null,
  to_ts bigint not null,
  detected_at bigint not null,
  in_session boolean not null,
  resolved boolean not null default false,
  primary key (asset, from_ts),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (from_ts >= 0 and to_ts > from_ts),
  check (from_ts % 60 = 0 and to_ts % 60 = 0),
  check (detected_at >= 0)
);

create table if not exists public.research_runs (
  run_id text primary key,
  started_at bigint not null,
  finished_at bigint,
  data_hash text,
  primitives_version text not null,
  seed bigint not null,
  candidates integer not null,
  approved integer not null,
  holdout_range text,
  coverage_pct numeric(5,2),
  status text not null check (status in ('ok','suspect','aborted')),
  check (started_at >= 0),
  check (finished_at is null or finished_at >= started_at),
  check (seed >= 0),
  check (candidates >= 0),
  check (approved >= 0 and approved <= candidates),
  check (coverage_pct is null or (coverage_pct >= 0 and coverage_pct <= 100))
);

create table if not exists public.manifests (
  manifest_version integer primary key,
  published_at bigint not null,
  expires_at bigint not null,
  storage_path text not null,
  sha256 text not null,
  signature text not null,
  primitives_version text not null,
  research_run_id text references public.research_runs(run_id),
  key_id text not null,
  check (manifest_version > 0),
  check (published_at >= 0),
  check (expires_at > published_at and expires_at - published_at <= 45 * 86400),
  check (sha256 ~ '^[0-9a-f]{64}$'),
  check (signature ~ '^ed25519:'),
  check (key_id in ('A','B'))
);

create table if not exists public.live_outcomes (
  client_id uuid not null,
  strategy_key text not null,
  ts bigint not null,
  won boolean not null,
  payout_pct numeric(5,2) not null,
  primary key (client_id, strategy_key, ts),
  check (ts >= 0 and ts % 60 = 0),
  check (payout_pct >= 0 and payout_pct <= 100)
);

create index if not exists live_outcomes_strategy_key_ts_idx
  on public.live_outcomes (strategy_key, ts);

create table if not exists public.collect_runs (
  run_id text primary key,
  started_at bigint not null,
  recorded_at bigint not null default extract(epoch from now())::bigint,
  report jsonb not null,
  status text not null check (status in ('ok','suspect','aborted')),
  check (started_at >= 0),
  check (recorded_at >= 0)
);

create table if not exists public.archive_jobs (
  job_id uuid primary key,
  from_ts bigint not null,
  to_ts bigint not null,
  expected_count bigint not null,
  requested_at bigint not null default extract(epoch from now())::bigint,
  completed_at bigint,
  archived_count bigint,
  status text not null check (status in ('requested','completed','failed')),
  check (from_ts >= 0 and to_ts > from_ts),
  check (expected_count >= 0),
  check (archived_count is null or archived_count >= 0),
  check (completed_at is null or completed_at >= requested_at)
);
