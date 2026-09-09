-- CAT-05: durable research evidence and persistent holdout reservations.
-- Metadata remains in Postgres; large TradeLogs/reports are private hash-addressed objects.

create table if not exists public.dataset_snapshots (
  fingerprint text primary key,
  origin text not null check (origin in ('supabase','parquet','synthetic','fixture')),
  source text not null,
  asset text not null,
  timeframe_s integer not null,
  from_ts bigint not null,
  to_ts bigint not null,
  present bigint not null,
  expected bigint not null,
  coverage numeric(12,10) not null,
  unresolved_in_session_gaps integer not null,
  tick_volume_available boolean not null,
  approval_eligible boolean not null,
  public_evidence jsonb not null,
  created_at bigint not null default extract(epoch from now())::bigint,
  check (fingerprint ~ '^sha256:[0-9a-f]{64}$'),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (timeframe_s in (60,300,900)),
  check (from_ts >= 0 and to_ts >= from_ts),
  check (from_ts % 60 = 0 and to_ts % 60 = 0),
  check (present >= 0 and expected >= present),
  check (coverage >= 0 and coverage <= 1),
  check (unresolved_in_session_gaps >= 0),
  check (created_at >= 0)
);

create index if not exists dataset_snapshots_asset_tf_range_idx
  on public.dataset_snapshots (asset, timeframe_s, from_ts, to_ts);

create table if not exists public.holdout_reservations (
  reservation_id text primary key,
  dataset_fingerprint text not null references public.dataset_snapshots(fingerprint),
  asset text not null,
  timeframe_s integer not null,
  from_ts bigint not null,
  to_ts bigint not null,
  holdout_hash text not null check (holdout_hash ~ '^[0-9a-f]{64}$'),
  reserved_by_run_id text not null,
  state text not null check (state in ('sealed','opened','burned','rolled_back')),
  created_at bigint not null default extract(epoch from now())::bigint,
  opened_at bigint,
  burned_at bigint,
  rolled_back_at bigint,
  unique (dataset_fingerprint, asset, timeframe_s, from_ts, to_ts),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (timeframe_s in (60,300,900)),
  check (from_ts >= 0 and to_ts > from_ts),
  check (from_ts % 60 = 0 and to_ts % 60 = 0),
  check (created_at >= 0),
  check (opened_at is null or opened_at >= created_at),
  check (burned_at is null or burned_at >= created_at),
  check (rolled_back_at is null or rolled_back_at >= created_at)
);

create index if not exists holdout_reservations_burned_idx
  on public.holdout_reservations (dataset_fingerprint, asset, timeframe_s, from_ts, to_ts)
  where state = 'burned';

create table if not exists public.research_attempts (
  run_id text not null,
  candidate_hash text not null,
  family text not null check (family in ('F1','F2','F3','F4','F5')),
  asset text not null,
  timeframe text not null check (timeframe in ('M1','M5','M15')),
  params_canonical jsonb not null,
  partition text not null check (partition in ('train_val','holdout','sanity')),
  method_version text not null,
  seed bigint not null,
  status text not null check (status in ('evaluated','rejected','pre_approved','approved')),
  approval_state text not null,
  counts jsonb not null,
  dataset_fingerprint text not null references public.dataset_snapshots(fingerprint),
  created_at bigint not null default extract(epoch from now())::bigint,
  primary key (run_id, candidate_hash, partition),
  check (candidate_hash ~ '^[0-9a-f]{64}$'),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (seed >= 0),
  check (created_at >= 0)
);

create index if not exists research_attempts_dataset_idx
  on public.research_attempts (dataset_fingerprint, family, status);

create table if not exists public.research_evidence_artifacts (
  sha256 text primary key,
  kind text not null check (kind in ('trade_log','ranking_md','candidates_json','report')),
  storage_path text not null,
  byte_count bigint not null,
  run_id text not null,
  candidate_hash text,
  created_at bigint not null default extract(epoch from now())::bigint,
  check (sha256 ~ '^[0-9a-f]{64}$'),
  check (byte_count > 0),
  check (candidate_hash is null or candidate_hash ~ '^[0-9a-f]{64}$'),
  check (created_at >= 0)
);

create index if not exists research_evidence_artifacts_run_idx
  on public.research_evidence_artifacts (run_id, kind);

alter table public.dataset_snapshots enable row level security;
alter table public.holdout_reservations enable row level security;
alter table public.research_attempts enable row level security;
alter table public.research_evidence_artifacts enable row level security;

revoke all on public.dataset_snapshots from anon, authenticated;
revoke all on public.holdout_reservations from anon, authenticated;
revoke all on public.research_attempts from anon, authenticated;
revoke all on public.research_evidence_artifacts from anon, authenticated;
