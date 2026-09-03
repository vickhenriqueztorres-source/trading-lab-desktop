-- P05 / R-HUB-2: RLS deny-by-default. Service role bypass is Supabase default.

alter table public.candles enable row level security;
alter table public.payouts enable row level security;
alter table public.market_sessions enable row level security;
alter table public.gaps enable row level security;
alter table public.research_runs enable row level security;
alter table public.manifests enable row level security;
alter table public.live_outcomes enable row level security;
alter table public.collect_runs enable row level security;
alter table public.archive_jobs enable row level security;

revoke all on public.candles from anon, authenticated;
revoke all on public.payouts from anon, authenticated;
revoke all on public.market_sessions from anon, authenticated;
revoke all on public.gaps from anon, authenticated;
revoke all on public.research_runs from anon, authenticated;
revoke all on public.manifests from anon, authenticated;
revoke all on public.live_outcomes from anon, authenticated;
revoke all on public.collect_runs from anon, authenticated;
revoke all on public.archive_jobs from anon, authenticated;

grant usage on schema public to anon;
grant insert on public.live_outcomes to anon;

drop policy if exists live_outcomes_anon_insert_own_client on public.live_outcomes;
create policy live_outcomes_anon_insert_own_client
  on public.live_outcomes
  for insert
  to anon
  with check (client_id = ((auth.jwt() ->> 'client_id')::uuid));
