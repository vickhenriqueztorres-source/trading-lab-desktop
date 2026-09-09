-- CAT-13: serialized, recoverable manifest publication saga.
-- Storage and Postgres are deliberately not described as one distributed transaction.

create table if not exists public.publication_journal (
  publication_id text primary key,
  channel text not null check (channel in ('staging','production')),
  manifest_version integer not null,
  sha256 text not null,
  storage_path text not null,
  research_run_id text not null,
  dataset_fingerprint text,
  dataset_kind text,
  key_id text not null,
  state text not null check (state in ('reserved','object_stored','committed','superseded')),
  failure_step text,
  failure_code text,
  projection_synced_at bigint,
  created_at bigint not null default extract(epoch from now())::bigint,
  updated_at bigint not null default extract(epoch from now())::bigint,
  unique (channel, manifest_version),
  check (manifest_version > 0),
  check (sha256 ~ '^[0-9a-f]{64}$'),
  check (storage_path ~ '^v[1-9][0-9]*[.]json$'),
  check (dataset_fingerprint is null or dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
  check (dataset_kind is null or dataset_kind in ('real_market','synthetic')),
  check (key_id in ('A','B')),
  check (failure_code is null or failure_code ~ '^[A-Z0-9_]{1,80}$'),
  check (created_at >= 0 and updated_at >= created_at),
  check (projection_synced_at is null or projection_synced_at >= created_at)
);

create table if not exists public.manifest_pointers (
  channel text primary key check (channel in ('staging','production')),
  manifest_version integer not null references public.manifests(manifest_version),
  storage_path text not null,
  sha256 text not null,
  generation bigint not null,
  committed_at bigint not null default extract(epoch from now())::bigint,
  check (storage_path ~ '^v[1-9][0-9]*[.]json$'),
  check (sha256 ~ '^[0-9a-f]{64}$'),
  check (generation > 0),
  check (committed_at >= 0)
);

create table if not exists public.manifest_mirror_outbox (
  publication_id text primary key references public.publication_journal(publication_id),
  manifest_version integer not null,
  storage_path text not null,
  expected_sha256 text not null,
  status text not null check (status in ('pending','processing','done','failed')),
  attempts integer not null default 0,
  next_attempt_at bigint not null default extract(epoch from now())::bigint,
  lease_expires_at bigint,
  last_error text,
  created_at bigint not null default extract(epoch from now())::bigint,
  completed_at bigint,
  check (manifest_version > 0),
  check (storage_path ~ '^v[1-9][0-9]*[.]json$'),
  check (expected_sha256 ~ '^[0-9a-f]{64}$'),
  check (attempts between 0 and 5),
  check (next_attempt_at >= 0),
  check (lease_expires_at is null or lease_expires_at >= 0),
  check (completed_at is null or completed_at >= created_at)
);

create index if not exists publication_journal_state_idx
  on public.publication_journal (channel, state, manifest_version);
create index if not exists manifest_mirror_outbox_pending_idx
  on public.manifest_mirror_outbox (status, next_attempt_at)
  where status in ('pending','failed','processing');

alter table public.research_evidence_artifacts
  drop constraint if exists research_evidence_artifacts_kind_check;
alter table public.research_evidence_artifacts
  add constraint research_evidence_artifacts_kind_check check (
    kind in (
      'trade_log','ranking_md','candidates_json','report',
      'portfolio_selection','manifest_draft'
    )
  );

create or replace function public.reserve_manifest_publication(
  p_channel text,
  p_manifest_version integer,
  p_sha256 text,
  p_storage_path text,
  p_research_run_id text,
  p_dataset_fingerprint text,
  p_dataset_kind text,
  p_key_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  existing public.publication_journal%rowtype;
  current_version integer;
  greatest_reserved integer;
  new_id text;
begin
  if p_channel not in ('staging','production') then
    raise exception 'HUB_PUBLICATION_CHANNEL_INVALID';
  end if;
  perform pg_advisory_xact_lock(hashtext('strategy-lab-manifest:' || p_channel));

  select * into existing
    from public.publication_journal
   where channel = p_channel and manifest_version = p_manifest_version;
  if found then
    if existing.sha256 <> p_sha256 then
      raise exception 'HUB_MANIFEST_VERSION_CONFLICT';
    end if;
    return jsonb_build_object(
      'publication_id', existing.publication_id,
      'state', existing.state,
      'idempotent', true
    );
  end if;

  select manifest_version into current_version
    from public.manifest_pointers where channel = p_channel;
  if current_version is not null and p_manifest_version <= current_version then
    raise exception 'HUB_MANIFEST_VERSION_REGRESSION';
  end if;
  select max(manifest_version) into greatest_reserved
    from public.publication_journal where channel = p_channel;
  if greatest_reserved is not null and p_manifest_version <= greatest_reserved then
    raise exception 'HUB_MANIFEST_VERSION_REGRESSION';
  end if;

  if p_channel = 'production' then
    if p_dataset_kind is distinct from 'real_market' or p_dataset_fingerprint is null then
      raise exception 'HUB_PRODUCTION_EVIDENCE_REQUIRED';
    end if;
    if not exists (
      select 1 from public.research_runs r
       where r.run_id = p_research_run_id
         and r.status = 'ok' and r.finished_at is not null
    ) then
      raise exception 'HUB_RESEARCH_RUN_NOT_SEALED';
    end if;
    if not exists (
      select 1 from public.dataset_snapshots d
       where d.fingerprint = p_dataset_fingerprint
         and d.approval_eligible
         and d.origin in ('supabase','parquet')
    ) then
      raise exception 'HUB_DATASET_EVIDENCE_INCOMPATIBLE';
    end if;
    if not exists (
      select 1 from public.research_attempts a
       where a.run_id = p_research_run_id
         and a.dataset_fingerprint = p_dataset_fingerprint
         and a.status = 'approved'
    ) then
      raise exception 'HUB_APPROVED_RESEARCH_EVIDENCE_REQUIRED';
    end if;
    if not exists (
      select 1 from public.research_evidence_artifacts e
       where e.run_id = p_research_run_id and e.kind = 'portfolio_selection'
    ) then
      raise exception 'HUB_PORTFOLIO_EVIDENCE_REQUIRED';
    end if;
  end if;

  new_id := p_channel || ':' || p_manifest_version::text;
  insert into public.publication_journal (
    publication_id, channel, manifest_version, sha256, storage_path,
    research_run_id, dataset_fingerprint, dataset_kind, key_id, state
  ) values (
    new_id, p_channel, p_manifest_version, p_sha256, p_storage_path,
    p_research_run_id, p_dataset_fingerprint, p_dataset_kind, p_key_id, 'reserved'
  );
  return jsonb_build_object('publication_id', new_id, 'state', 'reserved', 'idempotent', false);
end;
$$;

create or replace function public.mark_manifest_object_stored(p_publication_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.publication_journal
     set state = case when state = 'reserved' then 'object_stored' else state end,
         failure_step = null, failure_code = null,
         updated_at = extract(epoch from now())::bigint
   where publication_id = p_publication_id;
  if not found then raise exception 'HUB_PUBLICATION_NOT_FOUND'; end if;
end;
$$;

create or replace function public.commit_manifest_publication(
  p_publication_id text,
  p_published_at bigint,
  p_expires_at bigint,
  p_signature text,
  p_primitives_version text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  journal public.publication_journal%rowtype;
  pointer public.manifest_pointers%rowtype;
  existing_sha text;
  became_current boolean := false;
  next_generation bigint;
begin
  perform pg_advisory_xact_lock(hashtext('strategy-lab-manifest-commit'));
  select * into journal from public.publication_journal
   where publication_id = p_publication_id for update;
  if not found then raise exception 'HUB_PUBLICATION_NOT_FOUND'; end if;
  if journal.state = 'reserved' then raise exception 'HUB_OBJECT_NOT_CONFIRMED'; end if;
  if journal.state in ('committed','superseded') then
    select * into pointer from public.manifest_pointers where channel = journal.channel;
    return jsonb_build_object(
      'state', journal.state,
      'became_current', coalesce(pointer.manifest_version = journal.manifest_version, false),
      'generation', coalesce(pointer.generation, 0)
    );
  end if;

  insert into public.manifests (
    manifest_version, published_at, expires_at, storage_path, sha256,
    signature, primitives_version, research_run_id, key_id
  ) values (
    journal.manifest_version, p_published_at, p_expires_at,
    'manifests/' || journal.storage_path, journal.sha256,
    p_signature, p_primitives_version, journal.research_run_id, journal.key_id
  ) on conflict (manifest_version) do nothing;
  select sha256 into existing_sha from public.manifests
   where manifest_version = journal.manifest_version;
  if existing_sha is distinct from journal.sha256 then
    raise exception 'HUB_MANIFEST_VERSION_CONFLICT';
  end if;

  select * into pointer from public.manifest_pointers
   where channel = journal.channel for update;
  if not found or journal.manifest_version > pointer.manifest_version then
    next_generation := coalesce(pointer.generation, 0) + 1;
    insert into public.manifest_pointers (
      channel, manifest_version, storage_path, sha256, generation, committed_at
    ) values (
      journal.channel, journal.manifest_version, journal.storage_path,
      journal.sha256, next_generation, extract(epoch from now())::bigint
    ) on conflict (channel) do update set
      manifest_version = excluded.manifest_version,
      storage_path = excluded.storage_path,
      sha256 = excluded.sha256,
      generation = excluded.generation,
      committed_at = excluded.committed_at
      where public.manifest_pointers.manifest_version < excluded.manifest_version;
    became_current := true;
  else
    next_generation := pointer.generation;
  end if;

  update public.publication_journal
     set state = case when became_current then 'committed' else 'superseded' end,
         failure_step = null, failure_code = null,
         updated_at = extract(epoch from now())::bigint
   where publication_id = p_publication_id;
  insert into public.manifest_mirror_outbox (
    publication_id, manifest_version, storage_path, expected_sha256, status
  ) values (
    journal.publication_id, journal.manifest_version, journal.storage_path,
    journal.sha256, 'pending'
  ) on conflict (publication_id) do nothing;

  return jsonb_build_object(
    'state', case when became_current then 'committed' else 'superseded' end,
    'became_current', became_current,
    'generation', next_generation
  );
end;
$$;

create or replace function public.mark_manifest_projection_synced(p_publication_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.publication_journal j
     set projection_synced_at = extract(epoch from now())::bigint,
         updated_at = extract(epoch from now())::bigint
   where j.publication_id = p_publication_id
     and exists (
       select 1 from public.manifest_pointers p
        where p.channel = j.channel and p.manifest_version = j.manifest_version
     );
end;
$$;

create or replace function public.record_manifest_publication_failure(
  p_publication_id text,
  p_step text,
  p_code text
) returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.publication_journal
     set failure_step = left(p_step, 80), failure_code = left(p_code, 80),
         updated_at = extract(epoch from now())::bigint
   where publication_id = p_publication_id;
end;
$$;

create or replace function public.claim_manifest_mirror_job(p_publication_id text default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  job public.manifest_mirror_outbox%rowtype;
  now_epoch bigint := extract(epoch from now())::bigint;
begin
  select * into job from public.manifest_mirror_outbox
   where (p_publication_id is null or publication_id = p_publication_id)
     and attempts < 5
     and (
       (status in ('pending','failed') and next_attempt_at <= now_epoch)
       or (status = 'processing' and lease_expires_at <= now_epoch)
     )
   order by manifest_version
   for update skip locked limit 1;
  if not found then return null; end if;
  update public.manifest_mirror_outbox
     set status = 'processing', attempts = attempts + 1,
         lease_expires_at = now_epoch + 60
   where publication_id = job.publication_id
   returning * into job;
  return to_jsonb(job);
end;
$$;

create or replace function public.complete_manifest_mirror_job(p_publication_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.manifest_mirror_outbox
     set status = 'done', completed_at = extract(epoch from now())::bigint,
         lease_expires_at = null, last_error = null
   where publication_id = p_publication_id and status = 'processing';
end;
$$;

create or replace function public.fail_manifest_mirror_job(p_publication_id text, p_error text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  now_epoch bigint := extract(epoch from now())::bigint;
begin
  update public.manifest_mirror_outbox
     set status = 'failed', lease_expires_at = null,
         next_attempt_at = now_epoch + least(
           3600::bigint,
           (power(2::numeric, greatest(attempts, 1)) * 30)::bigint
         ),
         last_error = left(p_error, 160)
   where publication_id = p_publication_id and status = 'processing';
end;
$$;

alter table public.publication_journal enable row level security;
alter table public.manifest_pointers enable row level security;
alter table public.manifest_mirror_outbox enable row level security;
revoke all on public.publication_journal from anon, authenticated;
revoke all on public.manifest_pointers from anon, authenticated;
revoke all on public.manifest_mirror_outbox from anon, authenticated;

revoke all on function public.reserve_manifest_publication(text,integer,text,text,text,text,text,text)
  from public, anon, authenticated;
revoke all on function public.mark_manifest_object_stored(text)
  from public, anon, authenticated;
revoke all on function public.commit_manifest_publication(text,bigint,bigint,text,text)
  from public, anon, authenticated;
revoke all on function public.mark_manifest_projection_synced(text)
  from public, anon, authenticated;
revoke all on function public.record_manifest_publication_failure(text,text,text)
  from public, anon, authenticated;
revoke all on function public.claim_manifest_mirror_job(text)
  from public, anon, authenticated;
revoke all on function public.complete_manifest_mirror_job(text)
  from public, anon, authenticated;
revoke all on function public.fail_manifest_mirror_job(text,text)
  from public, anon, authenticated;

grant execute on function public.reserve_manifest_publication(text,integer,text,text,text,text,text,text)
  to service_role;
grant execute on function public.mark_manifest_object_stored(text) to service_role;
grant execute on function public.commit_manifest_publication(text,bigint,bigint,text,text)
  to service_role;
grant execute on function public.mark_manifest_projection_synced(text) to service_role;
grant execute on function public.record_manifest_publication_failure(text,text,text) to service_role;
grant execute on function public.claim_manifest_mirror_job(text) to service_role;
grant execute on function public.complete_manifest_mirror_job(text) to service_role;
grant execute on function public.fail_manifest_mirror_job(text,text) to service_role;
