-- P06 / R-HUB-4: per-client hourly rate limit state for outcomes.

create table if not exists public.rate_limits (
  bucket text not null,
  client_id uuid not null,
  window_start bigint not null,
  count integer not null default 0,
  primary key (bucket, client_id, window_start),
  check (bucket ~ '^[a-z_]{1,40}$'),
  check (window_start >= 0 and window_start % 3600 = 0),
  check (count >= 0)
);

alter table public.rate_limits enable row level security;
revoke all on public.rate_limits from anon, authenticated;

create or replace function public.consume_rate_limit(
  rate_bucket text,
  rate_client_id uuid,
  rate_window_start bigint,
  rate_limit integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  current_count integer;
begin
  insert into public.rate_limits(bucket, client_id, window_start, count)
  values (rate_bucket, rate_client_id, rate_window_start, 1)
  on conflict (bucket, client_id, window_start) do update
  set count = public.rate_limits.count + 1
  where public.rate_limits.count < rate_limit
  returning count into current_count;

  return current_count is not null;
end;
$$;
