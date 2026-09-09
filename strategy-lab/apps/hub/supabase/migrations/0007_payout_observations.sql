-- CAT-03 / R-COL-8 / R-RES-4: immutable point-in-time payout evidence.
-- Hourly averages remain diagnostic only and cannot be used as if known at hour start.

create table if not exists public.payout_observations (
  asset text not null,
  observed_at bigint not null,
  payout_pct numeric(5,2) not null,
  source text not null,
  primary key (asset, observed_at),
  check (asset ~ '^[A-Z0-9][A-Z0-9._-]{0,39}$'),
  check (observed_at >= 0),
  check (payout_pct >= 0 and payout_pct <= 100),
  check (length(source) between 1 and 200)
);

create index if not exists payout_observations_asset_observed_at_idx
  on public.payout_observations (asset, observed_at desc);

alter table public.payout_observations enable row level security;
revoke all on table public.payout_observations from anon, authenticated;
