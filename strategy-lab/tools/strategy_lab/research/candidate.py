"""Stable candidate identity for replay-only approval (R-RES-5)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

type ParamValue = Decimal | int | str
type ParamMap = Mapping[str, ParamValue]


@dataclass(frozen=True)
class Candidate:
    family: str
    regime: str
    trigger: str
    confirm: str
    params: Mapping[str, ParamMap] = field(default_factory=dict)
    tf: str = "M1"
    hours: tuple[int, int] = (0, 24)
    asset: str = "EURUSD-OTC"

    def stable_hash(self) -> str:
        """Return the public candidate hash; Decimal values are rendered canonically."""
        payload = {
            "asset": self.asset,
            "confirm": self.confirm,
            "family": self.family,
            "hours": list(self.hours),
            "params": {
                name: {key: _canonical_param(value) for key, value in sorted(params.items())}
                for name, params in sorted(self.params.items())
            },
            "regime": self.regime,
            "tf": self.tf,
            "trigger": self.trigger,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def hash(self) -> str:
        """Stable candidate hash (alias of stable_hash for contract parity)."""
        return self.stable_hash()

    def __hash__(self) -> int:
        return int(self.stable_hash()[:16], 16)

    def params_for(self, indicator_name: str) -> dict[str, ParamValue]:
        return dict(self.params.get(indicator_name, {}))


def _canonical_param(value: ParamValue) -> str | int:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value
