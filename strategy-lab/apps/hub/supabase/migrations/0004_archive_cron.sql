-- P05 / R-HUB-7: archive orchestration through pg_cron + pg_net.
-- pg_net is async, so deletion is a second explicit step after the archive function
-- reports the verified row count through complete_archive_job().

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create or replace function public.archive_old_candles()
returns uuid
language plpgsql
security definer
set search_path = public, extensions, net
as $$
declare
  cutoff bigint := ((extract(epoch from now())::bigint - 180 * 86400) / 60) * 60;
  oldest bigint;
  expected bigint;
  new_job_id uuid := gen_random_uuid();
  archive_url text := current_setting('app.settings.strategy_lab_archive_url', true);
  archive_token text := current_setting('app.settings.strategy_lab_archive_token', true);
begin
  if archive_url is null or archive_url = '' or archive_token is null or archive_token = '' then
    raise exception 'ARCHIVE_ENDPOINT_NOT_CONFIGURED';
  end if;

  select min(ts), count(*)
  into oldest, expected
  from public.candles
  where ts < cutoff;

  if expected = 0 or oldest is null then
    return null;
  end if;

  insert into public.archive_jobs(job_id, from_ts, to_ts, expected_count, status)
  values (new_job_id, oldest, cutoff, expected, 'requested');

  perform net.http_post(
    url := archive_url,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || archive_token
    ),
    body := jsonb_build_object(
      'job_id', new_job_id,
      'from_ts', oldest,
      'to_ts', cutoff,
      'expected_count', expected
    )
  );

  return new_job_id;
end;
$$;

create or replace function public.complete_archive_job(target_job_id uuid, archived_count bigint)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  job record;
  deleted_count bigint;
begin
  select *
  into job
  from public.archive_jobs
  where job_id = target_job_id
  for update;

  if not found then
    raise exception 'ARCHIVE_JOB_NOT_FOUND';
  end if;

  if job.status <> 'requested' then
    raise exception 'ARCHIVE_JOB_NOT_REQUESTED';
  end if;

  if job.expected_count <> archived_count then
    update public.archive_jobs
    set status = 'failed', archived_count = archived_count, completed_at = extract(epoch from now())::bigint
    where job_id = target_job_id;
    raise exception 'ARCHIVE_COUNT_MISMATCH';
  end if;

  delete from public.candles
  where ts >= job.from_ts and ts < job.to_ts;

  get diagnostics deleted_count = row_count;

  if deleted_count <> archived_count then
    raise exception 'ARCHIVE_DELETE_COUNT_MISMATCH';
  end if;

  update public.archive_jobs
  set status = 'completed', archived_count = archived_count, completed_at = extract(epoch from now())::bigint
  where job_id = target_job_id;

  return deleted_count;
end;
$$;

select cron.unschedule('strategy-lab-archive-old-candles')
where exists (
  select 1 from cron.job where jobname = 'strategy-lab-archive-old-candles'
);

select cron.schedule(
  'strategy-lab-archive-old-candles',
  '0 3 * * *',
  $$select public.archive_old_candles();$$
);
