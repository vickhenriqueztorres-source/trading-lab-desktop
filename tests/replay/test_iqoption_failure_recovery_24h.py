"""24h synthetic AUTO fault replay, no market or financial API connection."""

from collections import Counter
from dataclasses import replace

from apps.core.families import EvalResult
from packages.domain.models import Direction, OrderState
from tests.unit.test_iqoption_failure_recovery import setup_trader


def test_auto_failure_recovery_24h(capsys):
    trader, runtime, _, clock, config, _, _ = setup_trader(auto=True)
    config[0] = replace(config[0], max_daily_trades=100)
    counts = Counter()
    for info in trader._catalog_provider().active_strategies.values():
        info.instance.evaluate_detailed = lambda candles, context: EvalResult(
            Direction.CALL if int(clock[0]) // 60 % 60 in {0, 1, 2} else None,
            "OK" if int(clock[0]) // 60 % 60 in {0, 1, 2} else "NO_SIGNAL",
            len(candles),
            15,
            None,
            None,
            None,
        )
    # Two fresh entry opportunities each hour: rejection, unaffected asset,
    # then recovery on the next closed candle. Exactly 48 accepted fake orders.
    for minute in range(1440):
        clock[0] = minute * 60
        within_hour = minute % 60
        trader._scan_cursor = 1 if within_hour == 1 else 0
        runtime.reader.state = OrderState.REJECTED if within_hour == 0 else OrderState.ACCEPTED
        runtime.reader.outbox_reason = "IQOPTION_PURCHASE_TIME_EXPIRED"
        before = len(runtime.requests)
        trader._evaluate_cycle()
        if len(runtime.requests) > before:
            counts[runtime.reader.state.value] += 1
        else:
            counts["NO_SUBMIT"] += 1
        # Operator remains armed for all 24h; no begin_new_run or reset is called.
    assert counts == {"REJECTED": 24, "ACCEPTED": 48, "NO_SUBMIT": 1368}
    assert len({r.correlation_id for r in runtime.requests}) == 72
    assert not trader._failures.failures
    assert counts["ACCEPTED"] == trader._daily_trades_count
    with capsys.disabled():
        print("\n24h IQ AUTO failure replay:", dict(counts), "duplicate correlations=0")
