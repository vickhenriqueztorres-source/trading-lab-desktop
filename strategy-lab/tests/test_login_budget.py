"""R-COL-12: external authentication is limited across processes and restarts."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from strategy_lab.collect.iq_client import IQClient, IQClientError
from strategy_lab.collect.login_budget import LoginBudget, LoginBudgetError


def test_login_budget_allows_two_attempts_and_resets_next_utc_day(tmp_path):
    path = tmp_path / "budget.json"
    budget = LoginBudget(path, now=lambda: 10 * 86400)
    budget.consume()
    budget.consume()
    with pytest.raises(LoginBudgetError, match="IQ_LOGIN_DAILY_LIMIT"):
        budget.consume()
    LoginBudget(path, now=lambda: 11 * 86400).consume()


def test_login_budget_is_atomic_across_concurrent_clients(tmp_path):
    path = tmp_path / "budget.json"

    def attempt(_index):
        try:
            LoginBudget(path, now=lambda: 10 * 86400).consume()
            return "accepted"
        except LoginBudgetError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("accepted") == 2
    assert results.count("IQ_LOGIN_DAILY_LIMIT") == 6


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"schema_version":1,"day":10,"attempts":3}',
        '{"schema_version":1,"day":10,"attempts":1,"extra":0}',
        '{"schema_version":1,"day":10,"day":9,"attempts":1}',
    ],
)
def test_login_budget_corruption_fails_closed(tmp_path, payload):
    path = tmp_path / "budget.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(LoginBudgetError, match="IQ_LOGIN_BUDGET_STATE_INVALID"):
        LoginBudget(path, now=lambda: 10 * 86400).consume()


def test_external_client_budget_failure_precedes_backend_and_network():
    marker = []
    client = IQClient(
        credential_provider=lambda: marker.append("credential") or object(),  # type: ignore[arg-type]
        login_budget=lambda: (_ for _ in ()).throw(LoginBudgetError("IQ_LOGIN_DAILY_LIMIT")),
    )
    with pytest.raises(IQClientError, match="IQ_LOGIN_DAILY_LIMIT"):
        client.login()
    assert marker == ["credential"]
