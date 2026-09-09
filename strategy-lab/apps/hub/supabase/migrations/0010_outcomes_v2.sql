-- CAT-15 / R-HUB-2, R-HUB-4, R-RES-12: versioned, minimal outcome telemetry.
-- Legacy rows remain untouched. New reports are deduplicated before aggregation.

-- Direct Data API inserts bypass the Edge Function quotas. Keep the legacy table for
-- historical/query compatibility, but require every new anonymous write to cross /outcomes.
revoke insert on public.live_outcomes from anon;
drop policy if exists live_outcomes_anon_insert_own_client on public.live_outcomes;
revoke all on function public.consume_rate_limit(text, uuid, bigint, integer)
  from public, anon, authenticated;
grant execute on function public.consume_rate_limit(text, uuid, bigint, integer) to service_role;

create table if not exists public.live_outcome_events_v2 (
  client_id uuid not null,
  event_id uuid not null,
  schema_version smallint not null default 2,
  strategy_key text not null,
  recipe_revision bigint not null,
  manifest_version integer not null,
  execution_semantics_version text not null,
  primitives_version text not null,
  asset text not null,
  timeframe_s integer not null,
  product text not null,
  account_environment text not null,
  source text not null,
  signal_group_id text not null,
  ts bigint not null,
  won boolean not null,
  payout_pct numeric(5,2) not null,
  hub_environment text not null,
  received_at bigint not null default extract(epoch from now())::bigint,
  primary key (client_id, event_id),
  unique (client_id, strategy_key, recipe_revision, signal_group_id),
  check (schema_version = 2),
  check (length(strategy_key) between 1 and 120),
  check (recipe_revision > 0),
  check (manifest_version > 0),
  check (execution_semantics_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$'),
  check (primitives_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (timeframe_s in (60, 300, 900)),
  check (product in ('turbo', 'binary', 'digital')),
  check (account_environment in ('practice', 'real')),
  check (source = 'desktop_bot'),
  check (signal_group_id ~ '^sha256:[0-9a-f]{64}$'),
  check (ts >= 0 and ts % 60 = 0),
  check (payout_pct >= 0 and payout_pct <= 100),
  check (hub_environment in ('staging', 'production')),
  check (received_at >= 0)
);

create index if not exists live_outcome_events_v2_strategy_ts_idx
  on public.live_outcome_events_v2 (strategy_key, recipe_revision, ts);
create index if not exists live_outcome_events_v2_received_idx
  on public.live_outcome_events_v2 (received_at);

-- This table intentionally aggregates reports for one signal together. client_reports is
-- operational divergence evidence; it is NOT an independent statistical sample size.
create table if not exists public.live_outcome_signal_aggregates (
  strategy_key text not null,
  recipe_revision bigint not null,
  manifest_version integer not null,
  execution_semantics_version text not null,
  primitives_version text not null,
  asset text not null,
  timeframe_s integer not null,
  product text not null,
  account_environment text not null,
  signal_group_id text not null,
  signal_ts bigint not null,
  client_reports integer not null,
  won_reports integer not null,
  lost_reports integer not null,
  payout_pct_sum numeric(20,2) not null,
  first_received_at bigint not null,
  last_received_at bigint not null,
  primary key (strategy_key, recipe_revision, signal_group_id),
  check (client_reports > 0),
  check (won_reports >= 0 and lost_reports >= 0),
  check (won_reports + lost_reports = client_reports),
  check (payout_pct_sum >= 0),
  check (signal_ts >= 0 and signal_ts % 60 = 0),
  check (last_received_at >= first_received_at)
);

create index if not exists live_outcome_signal_aggregates_strategy_ts_idx
  on public.live_outcome_signal_aggregates (strategy_key, recipe_revision, signal_ts);

alter table public.live_outcome_events_v2 enable row level security;
alter table public.live_outcome_signal_aggregates enable row level security;
revoke all on public.live_outcome_events_v2 from anon, authenticated;
revoke all on public.live_outcome_signal_aggregates from anon, authenticated;

create or replace function public.ingest_live_outcomes_v2(p_rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  supplied integer;
  inserted integer;
begin
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'HUB_OUTCOME_BATCH_INVALID';
  end if;
  supplied := jsonb_array_length(p_rows);
  if supplied < 1 or supplied > 500 then
    raise exception 'HUB_OUTCOME_BATCH_INVALID';
  end if;

  with normalized as (
    select *
    from jsonb_to_recordset(p_rows) as row(
      client_id uuid,
      event_id uuid,
      schema_version smallint,
      strategy_key text,
      recipe_revision bigint,
      manifest_version integer,
      execution_semantics_version text,
      primitives_version text,
      asset text,
      timeframe_s integer,
      product text,
      account_environment text,
      source text,
      signal_group_id text,
      ts bigint,
      won boolean,
      payout_pct numeric,
      hub_environment text,
      received_at bigint
    )
  ), accepted as (
    insert into public.live_outcome_events_v2 (
      client_id, event_id, schema_version, strategy_key, recipe_revision,
      manifest_version, execution_semantics_version, primitives_version, asset,
      timeframe_s, product, account_environment, source, signal_group_id, ts,
      won, payout_pct, hub_environment, received_at
    )
    select
      client_id, event_id, schema_version, strategy_key, recipe_revision,
      manifest_version, execution_semantics_version, primitives_version, asset,
      timeframe_s, product, account_environment, source, signal_group_id, ts,
      won, payout_pct, hub_environment, received_at
    from normalized
    on conflict do nothing
    returning *
  ), aggregated as (
    insert into public.live_outcome_signal_aggregates (
      strategy_key, recipe_revision, manifest_version, execution_semantics_version,
      primitives_version, asset, timeframe_s, product, account_environment,
      signal_group_id, signal_ts, client_reports, won_reports, lost_reports,
      payout_pct_sum, first_received_at, last_received_at
    )
    select
      strategy_key, recipe_revision, manifest_version, execution_semantics_version,
      primitives_version, asset, timeframe_s, product, account_environment,
      signal_group_id, ts, count(*)::integer,
      count(*) filter (where won)::integer,
      count(*) filter (where not won)::integer,
      sum(payout_pct), min(received_at), max(received_at)
    from accepted
    group by strategy_key, recipe_revision, manifest_version,
      execution_semantics_version, primitives_version, asset, timeframe_s,
      product, account_environment, signal_group_id, ts
    on conflict (strategy_key, recipe_revision, signal_group_id) do update set
      client_reports = public.live_outcome_signal_aggregates.client_reports + excluded.client_reports,
      won_reports = public.live_outcome_signal_aggregates.won_reports + excluded.won_reports,
      lost_reports = public.live_outcome_signal_aggregates.lost_reports + excluded.lost_reports,
      payout_pct_sum = public.live_outcome_signal_aggregates.payout_pct_sum + excluded.payout_pct_sum,
      first_received_at = least(
        public.live_outcome_signal_aggregates.first_received_at,
        excluded.first_received_at
      ),
      last_received_at = greatest(
        public.live_outcome_signal_aggregates.last_received_at,
        excluded.last_received_at
      )
    returning 1
  )
  select count(*)::integer into inserted from accepted;

  return jsonb_build_object('received', supplied, 'inserted', inserted,
    'duplicates', supplied - inserted);
end;
$$;

revoke all on function public.ingest_live_outcomes_v2(jsonb) from public, anon, authenticated;
grant execute on function public.ingest_live_outcomes_v2(jsonb) to service_role;

-- Retention is time-bounded. Only normalized minimal rows are stored, never raw request bodies.
create or replace function public.purge_old_outcome_events_v2(p_now bigint)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  removed integer;
begin
  delete from public.live_outcome_events_v2
  where received_at < p_now - 90 * 86400;
  get diagnostics removed = row_count;
  return removed;
end;
$$;

revoke all on function public.purge_old_outcome_events_v2(bigint) from public, anon, authenticated;
grant execute on function public.purge_old_outcome_events_v2(bigint) to service_role;

do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.unschedule(jobid)
    from cron.job
    where jobname = 'strategy_lab_purge_outcomes_v2_daily';
    perform cron.schedule(
      'strategy_lab_purge_outcomes_v2_daily',
      '17 3 * * *',
      'select public.purge_old_outcome_events_v2(extract(epoch from now())::bigint)'
    );
  end if;
end;
$$;
