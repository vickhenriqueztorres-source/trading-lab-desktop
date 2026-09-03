-- P05 / R-HUB-1: initial market session calendar.
-- Forex: Monday 00:00 UTC through Friday 21:00 UTC.
-- OTC: Saturday 00:00 UTC through Sunday 24:00 UTC.

insert into public.market_sessions (asset, weekday, open_min, close_min)
select asset, weekday, open_min, close_min
from (
  values
    ('EURUSD', 0, 0, 1440),
    ('EURUSD', 1, 0, 1440),
    ('EURUSD', 2, 0, 1440),
    ('EURUSD', 3, 0, 1440),
    ('EURUSD', 4, 0, 1260),
    ('GBPUSD', 0, 0, 1440),
    ('GBPUSD', 1, 0, 1440),
    ('GBPUSD', 2, 0, 1440),
    ('GBPUSD', 3, 0, 1440),
    ('GBPUSD', 4, 0, 1260),
    ('USDJPY', 0, 0, 1440),
    ('USDJPY', 1, 0, 1440),
    ('USDJPY', 2, 0, 1440),
    ('USDJPY', 3, 0, 1440),
    ('USDJPY', 4, 0, 1260),
    ('AUDUSD', 0, 0, 1440),
    ('AUDUSD', 1, 0, 1440),
    ('AUDUSD', 2, 0, 1440),
    ('AUDUSD', 3, 0, 1440),
    ('AUDUSD', 4, 0, 1260),
    ('EURJPY', 0, 0, 1440),
    ('EURJPY', 1, 0, 1440),
    ('EURJPY', 2, 0, 1440),
    ('EURJPY', 3, 0, 1440),
    ('EURJPY', 4, 0, 1260),
    ('EURUSD-OTC', 5, 0, 1440),
    ('EURUSD-OTC', 6, 0, 1440),
    ('GBPUSD-OTC', 5, 0, 1440),
    ('GBPUSD-OTC', 6, 0, 1440),
    ('USDJPY-OTC', 5, 0, 1440),
    ('USDJPY-OTC', 6, 0, 1440),
    ('AUDUSD-OTC', 5, 0, 1440),
    ('AUDUSD-OTC', 6, 0, 1440),
    ('EURJPY-OTC', 5, 0, 1440),
    ('EURJPY-OTC', 6, 0, 1440)
) as seed(asset, weekday, open_min, close_min)
on conflict (asset, weekday, open_min) do update
set close_min = excluded.close_min;
