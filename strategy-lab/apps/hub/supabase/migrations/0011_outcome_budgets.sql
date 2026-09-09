-- CAT-15: weighted, atomic budgets prevent large batches and rotating UUIDs from
-- multiplying Supabase storage/invocations without a global ceiling.

create or replace function public.consume_weighted_rate_limit(
  rate_bucket text,
  rate_client_id uuid,
  rate_window_start bigint,
  rate_limit integer,
  rate_cost integer
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  current_count integer;
begin
  if rate_cost < 1 or rate_limit < rate_cost then
    return false;
  end if;
  insert into public.rate_limits(bucket, client_id, window_start, count)
  values (rate_bucket, rate_client_id, rate_window_start, rate_cost)
  on conflict (bucket, client_id, window_start) do update
  set count = public.rate_limits.count + excluded.count
  where public.rate_limits.count <= rate_limit - excluded.count
  returning count into current_count;
  return current_count is not null;
end;
$$;

revoke all on function public.consume_weighted_rate_limit(text, uuid, bigint, integer, integer)
  from public, anon, authenticated;
grant execute on function public.consume_weighted_rate_limit(text, uuid, bigint, integer, integer)
  to service_role;

create or replace function public.consume_outcome_budgets(
  rate_client_id uuid,
  rate_hour_start bigint,
  rate_day_start bigint,
  event_count integer
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  global_id constant uuid := '00000000-0000-4000-8000-000000000000'::uuid;
begin
  begin
    if not public.consume_weighted_rate_limit(
      'outcomes_global_requests', global_id, rate_hour_start, 600, 1
    ) then raise exception 'OUTCOME_GLOBAL_RATE_LIMITED'; end if;
    if not public.consume_weighted_rate_limit(
      'outcomes_global_events', global_id, rate_day_start, 5000, event_count
    ) then raise exception 'OUTCOME_GLOBAL_EVENT_BUDGET_EXHAUSTED'; end if;
    if not public.consume_weighted_rate_limit(
      'outcomes_client_requests', rate_client_id, rate_hour_start, 60, 1
    ) then raise exception 'OUTCOME_RATE_LIMITED'; end if;
    if not public.consume_weighted_rate_limit(
      'outcomes_client_events', rate_client_id, rate_day_start, 500, event_count
    ) then raise exception 'OUTCOME_CLIENT_EVENT_BUDGET_EXHAUSTED'; end if;
    return 'ALLOWED';
  exception when raise_exception then
    -- PL/pgSQL exception blocks are subtransactions. Earlier counters in this block
    -- roll back, so a rejected request cannot leak only part of its budget.
    return sqlerrm;
  end;
end;
$$;

revoke all on function public.consume_outcome_budgets(uuid, bigint, bigint, integer)
  from public, anon, authenticated;
grant execute on function public.consume_outcome_budgets(uuid, bigint, bigint, integer)
  to service_role;

create or replace function public.purge_old_outcome_events_v2(p_now bigint)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  removed integer;
  removed_aggregates integer;
begin
  delete from public.live_outcome_events_v2
  where received_at < p_now - 90 * 86400;
  get diagnostics removed = row_count;
  delete from public.live_outcome_signal_aggregates
  where last_received_at < p_now - 400 * 86400;
  get diagnostics removed_aggregates = row_count;
  return removed + removed_aggregates;
end;
$$;

revoke all on function public.purge_old_outcome_events_v2(bigint) from public, anon, authenticated;
grant execute on function public.purge_old_outcome_events_v2(bigint) to service_role;
