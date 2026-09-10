from pathlib import Path

from packages.brokers.iqoption.ssid_store import SsidStore
from packages.security import SecretValue, SimulatedUserScopedVault


def _store(
    tmp_path: Path,
    now: list[float],
    backing: dict[str, bytes],
    *,
    ttl: int = 100,
) -> SsidStore:
    return SsidStore(
        tmp_path,
        ttl_seconds=ttl,
        wall_time=lambda: now[0],
        vault=SimulatedUserScopedVault("test-user", backing),
    )


def test_save_and_load_session_by_account_mode(tmp_path: Path) -> None:
    now = [1_000.0]
    backing: dict[str, bytes] = {}
    store = _store(tmp_path, now, backing)
    store.save(SecretValue.from_text("ssid-secret"), "practice")

    loaded = store.load("practice")
    assert loaded is not None
    assert loaded.ssid.reveal_text() == "ssid-secret"
    assert store.load("real") is None


def test_expired_session_is_removed(tmp_path: Path) -> None:
    now = [2_000.0]
    backing: dict[str, bytes] = {}
    store = _store(tmp_path, now, backing, ttl=10)
    store.save(SecretValue.from_text("ssid-secret"), "practice")

    now[0] += 11
    assert store.load("practice") is None
    assert not backing


def test_corrupt_session_is_removed(tmp_path: Path) -> None:
    now = [3_000.0]
    backing: dict[str, bytes] = {}
    vault = SimulatedUserScopedVault("test-user", backing)
    store = SsidStore(tmp_path, wall_time=lambda: now[0], vault=vault)
    vault.set_secret("iqoption.session.practice", SecretValue.from_text("not-json"))

    assert store.load("practice") is None
    assert not backing
