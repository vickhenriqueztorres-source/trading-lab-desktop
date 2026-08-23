from typing import TYPE_CHECKING, Any

from packages.replay.clock import ReplayClock, ReplayClockPort
from packages.replay.models import (
    ReplayRecord,
    ReplayRequest,
    ReplayResult,
    ReplayRiskDecision,
    ReplayStatus,
    configuration_hash_for,
)

if TYPE_CHECKING:
    from packages.replay.engine import (
        CheckpointRestoreError,
        CheckpointRestoreReason,
        ReplayEngine,
        ReplayPersistence,
        ReplaySession,
    )

_ENGINE_EXPORTS = frozenset(
    {
        "CheckpointRestoreError",
        "CheckpointRestoreReason",
        "ReplayEngine",
        "ReplayPersistence",
        "ReplaySession",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _ENGINE_EXPORTS:
        raise AttributeError(name)
    from packages.replay import engine

    return getattr(engine, name)


__all__ = [
    "CheckpointRestoreError",
    "CheckpointRestoreReason",
    "ReplayClock",
    "ReplayClockPort",
    "ReplayEngine",
    "ReplayPersistence",
    "ReplayRequest",
    "ReplayRecord",
    "ReplayResult",
    "ReplayRiskDecision",
    "ReplaySession",
    "ReplayStatus",
    "configuration_hash_for",
]
