-- CAT-18: retention is an auditable, closed-plan operation.
-- The application never issues wildcard deletes and records the inspected fingerprint.
create table if not exists public.retention_plans (
    plan_hash text primary key check (plan_hash ~ '^[0-9a-f]{64}$'),
    project_ref text not null check (length(project_ref) between 1 and 128),
    environment text not null check (environment in ('staging', 'production')),
    generated_at bigint not null check (generated_at >= 0),
    snapshot_hash text not null check (snapshot_hash ~ '^[0-9a-f]{64}$'),
    policy jsonb not null,
    target_count bigint not null check (target_count >= 0),
    status text not null default 'planned' check (status in ('planned', 'applied', 'aborted')),
    created_by text not null default 'strategy-lab',
    created_at timestamptz not null default now()
);

create table if not exists public.retention_plan_targets (
    plan_hash text not null references public.retention_plans(plan_hash) on delete restrict,
    target_id text not null check (length(target_id) between 1 and 512 and target_id !~ '[*?]'),
    category text not null check (category in ('temporary', 'publication_orphan', 'staging_expired', 'archived_log', 'cold_candle')),
    size_bytes bigint not null check (size_bytes >= 0),
    content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
    version bigint not null check (version >= 1),
    references_json jsonb not null default '[]'::jsonb,
    primary key (plan_hash, target_id)
);

create table if not exists public.retention_events (
    event_id uuid primary key default gen_random_uuid(),
    plan_hash text not null references public.retention_plans(plan_hash) on delete restrict,
    event_type text not null check (event_type in ('planned', 'applied', 'aborted', 'target_changed')),
    event_at timestamptz not null default now(),
    details jsonb not null default '{}'::jsonb
);

create index if not exists retention_plans_status_idx on public.retention_plans(status, created_at desc);
create index if not exists retention_events_plan_idx on public.retention_events(plan_hash, event_at desc);

alter table public.retention_plans enable row level security;
alter table public.retention_plan_targets enable row level security;
alter table public.retention_events enable row level security;
revoke all on public.retention_plans from anon, authenticated;
revoke all on public.retention_plan_targets from anon, authenticated;
revoke all on public.retention_events from anon, authenticated;
