-- CAT-17 / R-HUB-7: avoid one tiny Parquet object per sparse daily cron run.
-- A slice remains hot until it has at least 1,000 rows; normal M1 30-day slices are much larger.

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
  existing_job_id uuid;
  frozen_count bigint;
begin
  with candidates as (
    select c.asset, min(c.ts) as from_ts
    from public.candles c
    where c.ts < cutoff
      and not exists (
        select 1 from public.cold_archive_jobs j
        where j.asset = c.asset
          and j.status in ('requested','claimed','uploaded','verified')
      )
    group by c.asset
  ), eligible as (
    select candidate.asset, candidate.from_ts,
           least(cutoff, candidate.from_ts + 30 * 86400) as to_ts
    from candidates candidate
    where (
      select count(*) from public.candles c
      where c.asset = candidate.asset
        and c.ts >= candidate.from_ts
        and c.ts < least(cutoff, candidate.from_ts + 30 * 86400)
    ) >= 1000
  )
  select asset, from_ts, to_ts
  into selected_asset, selected_from, selected_to
  from eligible
  order by from_ts, asset
  limit 1;

  if selected_asset is null or selected_from is null or selected_to is null then
    return null;
  end if;

  perform pg_advisory_xact_lock(hashtext('cold-archive:' || selected_asset));
  select job_id into existing_job_id
  from public.cold_archive_jobs
  where asset = selected_asset and status in ('requested','claimed','uploaded','verified')
  order by requested_at
  limit 1;
  if existing_job_id is not null then
    return existing_job_id;
  end if;
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
  if frozen_count < 1000 then
    raise exception 'ARCHIVE_PARTITION_FLOOR_NOT_MET';
  end if;
  update public.cold_archive_jobs set expected_count = frozen_count where job_id = new_job_id;
  return new_job_id;
end;
$$;

revoke all on function public.archive_old_candles() from public, anon, authenticated;
grant execute on function public.archive_old_candles() to service_role;
