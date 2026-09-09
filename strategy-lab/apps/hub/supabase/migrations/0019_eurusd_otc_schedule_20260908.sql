-- R-COL-7: point-in-time schedule observed from api_option_init_all on 2026-09-08.
-- EURUSD-OTC was offered every UTC day with a daily 08:00-08:30 UTC break.
-- Do not generalize this evidence to another symbol; each asset remains distinct.

delete from public.market_sessions
where asset = 'EURUSD-OTC';

insert into public.market_sessions(asset, weekday, open_min, close_min)
select 'EURUSD-OTC', weekday, open_min, close_min
from generate_series(0, 6) as days(weekday)
cross join (values (0, 480), (510, 1440)) as session_window(open_min, close_min);

-- Reclassify existing gaps fail-closed: any overlap with an observed open
-- minute makes the whole recorded gap relevant to the coverage gate.
update public.gaps as gap
set in_session = exists (
    select 1
    from generate_series(gap.from_ts, gap.to_ts - 60, 60) as minute(ts)
    join public.market_sessions as session
      on session.asset = gap.asset
     and session.weekday = ((minute.ts / 86400 + 3) % 7)::integer
     and session.open_min <= ((minute.ts % 86400) / 60)::integer
     and session.close_min > ((minute.ts % 86400) / 60)::integer
)
where gap.asset = 'EURUSD-OTC';
