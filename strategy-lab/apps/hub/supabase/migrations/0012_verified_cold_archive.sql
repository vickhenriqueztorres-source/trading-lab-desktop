-- CAT-17 / R-HUB-7 / R-OPS-1: verified, resumable cold archive protocol.
-- The former complete_archive_job(uuid,bigint) trusted a caller-supplied row count and
-- deleted a broad time range.  It is disabled below before any safe completion path exists.

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.collection_watermarks (
  asset text primary key,
  max_closed_ts bigint not null,
  updated_at bigint not null default extract(epoch from now())::bigint,
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (max_closed_ts >= 0 and max_closed_ts % 60 = 0),
  check (updated_at >= 0)
);

create table if not exists public.cold_archive_jobs (
  job_id uuid primary key default gen_random_uuid(),
  asset text not null,
  from_ts bigint not null,
  to_ts bigint not null,
  schema_version integer not null default 1,
  expected_count bigint not null,
  snapshot_sha256 text,
  object_path text,
  object_sha256 text,
  object_bytes bigint,
  claim_token uuid,
  claimed_by text,
  claim_expires_at bigint,
  requested_at bigint not null default extract(epoch from now())::bigint,
  verified_at bigint,
  completed_at bigint,
  deleted_count bigint,
  status text not null default 'requested'
    check (status in ('requested','claimed','uploaded','verified','completed','failed')),
  error_code text,
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (from_ts >= 0 and from_ts % 60 = 0),
  check (to_ts > from_ts and to_ts % 60 = 0),
  check (schema_version = 1),
  check (expected_count > 0),
  check (snapshot_sha256 is null or snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  check (object_sha256 is null or object_sha256 ~ '^[0-9a-f]{64}$'),
  check (object_bytes is null or object_bytes > 0),
  check (deleted_count is null or deleted_count >= 0)
);

create index if not exists cold_archive_jobs_status_requested_idx
  on public.cold_archive_jobs (status, requested_at);
create index if not exists cold_archive_jobs_asset_range_idx
  on public.cold_archive_jobs (asset, from_ts, to_ts);
create unique index if not exists cold_archive_jobs_active_slice_unique_idx
  on public.cold_archive_jobs (asset, from_ts, to_ts)
  where status <> 'failed';

create table if not exists public.cold_archive_job_rows (
  job_id uuid not null references public.cold_archive_jobs(job_id) on delete restrict,
  asset text not null,
  ts bigint not null,
  o numeric(18,8) not null,
  h numeric(18,8) not null,
  l numeric(18,8) not null,
  c numeric(18,8) not null,
  tick_vol integer not null,
  source text not null,
  collected_at bigint not null,
  payout_observations jsonb not null default '[]'::jsonb,
  primary key (job_id, asset, ts),
  check (ts >= 0 and ts % 60 = 0),
  check (l <= least(o,c) and greatest(o,c) <= h),
  check (tick_vol >= 0),
  check (jsonb_typeof(payout_observations) = 'array')
);

create table if not exists public.cold_archive_objects (
  object_path text primary key,
  job_id uuid not null unique references public.cold_archive_jobs(job_id) on delete restrict,
  object_sha256 text not null,
  content_sha256 text not null,
  schema_version integer not null,
  row_count bigint not null,
  object_bytes bigint not null,
  verified_at bigint not null,
  check (object_path ~ '^parquet/[A-Z0-9._-]+/[0-9]{10}-[0-9]{10}-[0-9a-f]{16}\\.parquet$'),
  check (object_sha256 ~ '^[0-9a-f]{64}$'),
  check (content_sha256 ~ '^[0-9a-f]{64}$'),
  check (schema_version = 1),
  check (row_count > 0 and object_bytes > 0),
  check (verified_at >= 0)
);

alter table public.collection_watermarks enable row level security;
alter table public.cold_archive_jobs enable row level security;
alter table public.cold_archive_job_rows enable row level security;
alter table public.cold_archive_objects enable row level security;

revoke all on public.collection_watermarks from public, anon, authenticated;
revoke all on public.cold_archive_jobs from public, anon, authenticated;
revoke all on public.cold_archive_job_rows from public, anon, authenticated;
revoke all on public.cold_archive_objects from public, anon, authenticated;

-- This signature is kept only to make every old caller fail closed after migration.
create or replace function public.complete_archive_job(target_job_id uuid, archived_count bigint)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception 'ARCHIVE_UNSAFE_COMPLETION_DISABLED';
end;
$$;
revoke all on function public.complete_archive_job(uuid, bigint) from public, anon, authenticated;

create or replace function public.archive_old_candles()
returns uuid
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  cutoff bigint := ((extract(epoch from now())::bigint - 180 * 86400) / 60) * 60;
  selected_asset text;
  selected_from bigint;
  selected_to bigint;
  new_job_id uuid := gen_random_uuid();
  frozen_count bigint;
begin
  -- At most one stable 30-day slice is planned per invocation.  Repeated cron runs drain
  -- backlog without creating a burst of tiny files.
  select c.asset, min(c.ts)
  into selected_asset, selected_from
  from public.candles c
  where c.ts < cutoff
    and not exists (
      select 1 from public.cold_archive_jobs j
      where j.asset = c.asset
        and j.status in ('requested','claimed','uploaded','verified')
    )
  group by c.asset
  order by min(c.ts), c.asset
  limit 1;

  if selected_asset is null or selected_from is null then
    return null;
  end if;

  perform pg_advisory_xact_lock(hashtext('cold-archive:' || selected_asset));
  selected_to := least(cutoff, selected_from + 30 * 86400);

  insert into public.cold_archive_jobs(
    job_id, asset, from_ts, to_ts, expected_count, status
  ) values (
    new_job_id, selected_asset, selected_from, selected_to, 1, 'requested'
  );

  insert into public.cold_archive_job_rows(
    job_id, asset, ts, o, h, l, c, tick_vol, source, collected_at,
    payout_observations
  )
  select new_job_id, c.asset, c.ts, c.o, c.h, c.l, c.c, c.tick_vol, c.source,
         c.collected_at,
         coalesce((
           select jsonb_agg(
             jsonb_build_object(
               'observed_at', po.observed_at,
               'payout_pct', to_char(po.payout_pct, 'FM990.00'),
               'source', po.source
             ) order by po.observed_at
           )
           from public.payout_observations po
           where po.asset = c.asset and po.observed_at >= c.ts and po.observed_at < c.ts + 60
         ), '[]'::jsonb)
  from public.candles c
  where c.asset = selected_asset and c.ts >= selected_from and c.ts < selected_to
  order by c.ts;

  get diagnostics frozen_count = row_count;
  if frozen_count = 0 then
    delete from public.cold_archive_jobs where job_id = new_job_id;
    return null;
  end if;
  update public.cold_archive_jobs set expected_count = frozen_count where job_id = new_job_id;
  return new_job_id;
end;
$$;

create or replace function public.claim_cold_archive_job(worker_id text, lease_seconds integer)
returns setof public.cold_archive_jobs
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  selected_id uuid;
  now_epoch bigint := extract(epoch from now())::bigint;
begin
  if worker_id is null or worker_id = '' or lease_seconds < 60 or lease_seconds > 3600 then
    raise exception 'ARCHIVE_CLAIM_INVALID';
  end if;
  select job_id into selected_id
  from public.cold_archive_jobs
  where status = 'requested'
     or (status in ('claimed','uploaded','verified') and claim_expires_at < now_epoch)
  order by requested_at, job_id
  for update skip locked
  limit 1;
  if selected_id is null then
    return;
  end if;
  update public.cold_archive_jobs
  set status = 'claimed', claim_token = gen_random_uuid(), claimed_by = worker_id,
      claim_expires_at = now_epoch + lease_seconds, error_code = null
  where job_id = selected_id;
  return query select * from public.cold_archive_jobs where job_id = selected_id;
end;
$$;

create or replace function public.verify_cold_archive_object(
  target_job_id uuid,
  target_claim_token uuid,
  target_snapshot_sha256 text,
  target_object_path text,
  target_object_sha256 text,
  target_object_bytes bigint,
  target_row_count bigint
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  job public.cold_archive_jobs%rowtype;
begin
  select * into job from public.cold_archive_jobs where job_id = target_job_id for update;
  if not found or job.status not in ('claimed','uploaded')
     or job.claim_token <> target_claim_token
     or job.claim_expires_at < extract(epoch from now())::bigint then
    raise exception 'ARCHIVE_CLAIM_INVALID';
  end if;
  if target_row_count <> job.expected_count
     or target_snapshot_sha256 !~ '^[0-9a-f]{64}$'
     or target_object_sha256 !~ '^[0-9a-f]{64}$'
     or target_object_bytes <= 0 then
    raise exception 'ARCHIVE_PROOF_INVALID';
  end if;
  insert into public.cold_archive_objects(
    object_path, job_id, object_sha256, content_sha256, schema_version,
    row_count, object_bytes, verified_at
  ) values (
    target_object_path, target_job_id, target_object_sha256, target_snapshot_sha256, 1,
    target_row_count, target_object_bytes, extract(epoch from now())::bigint
  ) on conflict (job_id) do nothing;
  if not exists (
    select 1 from public.cold_archive_objects o
    where o.job_id = target_job_id
      and o.object_path = target_object_path
      and o.object_sha256 = target_object_sha256
      and o.content_sha256 = target_snapshot_sha256
      and o.row_count = target_row_count
      and o.object_bytes = target_object_bytes
  ) then
    raise exception 'ARCHIVE_IMMUTABLE_PROOF_CONFLICT';
  end if;
  update public.cold_archive_jobs
  set status = 'verified', snapshot_sha256 = target_snapshot_sha256,
      object_path = target_object_path, object_sha256 = target_object_sha256,
      object_bytes = target_object_bytes, verified_at = extract(epoch from now())::bigint
  where job_id = target_job_id;
end;
$$;

create or replace function public.complete_verified_cold_archive_job(
  target_job_id uuid,
  target_claim_token uuid
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  job public.cold_archive_jobs%rowtype;
  matching_count bigint;
  current_count bigint;
  removed bigint;
begin
  select * into job from public.cold_archive_jobs where job_id = target_job_id for update;
  if not found or job.status <> 'verified' or job.claim_token <> target_claim_token then
    raise exception 'ARCHIVE_NOT_VERIFIED';
  end if;

  select count(*) into current_count
  from public.candles c
  join public.cold_archive_job_rows r
    on r.job_id = target_job_id and r.asset = c.asset and r.ts = c.ts;
  select count(*) into matching_count
  from public.candles c
  join public.cold_archive_job_rows r
    on r.job_id = target_job_id and r.asset = c.asset and r.ts = c.ts
   and r.o = c.o and r.h = c.h and r.l = c.l and r.c = c.c
   and r.tick_vol = c.tick_vol and r.source = c.source and r.collected_at = c.collected_at;

  if current_count <> job.expected_count or matching_count <> job.expected_count then
    update public.cold_archive_jobs
    set status = 'failed', error_code = 'ARCHIVE_LATE_UPDATE_DETECTED'
    where job_id = target_job_id;
    return 0;
  end if;

  delete from public.candles c
  using public.cold_archive_job_rows r
  where r.job_id = target_job_id and r.asset = c.asset and r.ts = c.ts
    and r.o = c.o and r.h = c.h and r.l = c.l and r.c = c.c
    and r.tick_vol = c.tick_vol and r.source = c.source and r.collected_at = c.collected_at;
  get diagnostics removed = row_count;
  if removed <> job.expected_count then
    raise exception 'ARCHIVE_EXACT_DELETE_MISMATCH';
  end if;
  update public.cold_archive_jobs
  set status = 'completed', deleted_count = removed,
      completed_at = extract(epoch from now())::bigint
  where job_id = target_job_id;
  return removed;
end;
$$;

revoke all on function public.archive_old_candles() from public, anon, authenticated;
revoke all on function public.claim_cold_archive_job(text, integer) from public, anon, authenticated;
revoke all on function public.verify_cold_archive_object(
  uuid, uuid, text, text, text, bigint, bigint
) from public, anon, authenticated;
revoke all on function public.complete_verified_cold_archive_job(uuid, uuid)
  from public, anon, authenticated;

grant execute on function public.archive_old_candles() to service_role;
grant execute on function public.claim_cold_archive_job(text, integer) to service_role;
grant execute on function public.verify_cold_archive_object(
  uuid, uuid, text, text, text, bigint, bigint
) to service_role;
grant execute on function public.complete_verified_cold_archive_job(uuid, uuid) to service_role;

select cron.unschedule('strategy-lab-archive-old-candles')
where exists (select 1 from cron.job where jobname = 'strategy-lab-archive-old-candles');
select cron.schedule(
  'strategy-lab-archive-old-candles',
  '0 3 * * *',
  $$select public.archive_old_candles();$$
);
