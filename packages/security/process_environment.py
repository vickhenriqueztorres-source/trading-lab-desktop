from __future__ import annotations

import os
from collections.abc import Mapping

_BROKER_SECRET_PREFIXES = ("DUALTRADE_DERIV_", "DUALTRADE_IQOPTION_")


def without_broker_credentials(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child environment without reading broker credential values."""

    source = os.environ if environment is None else environment
    sanitized: dict[str, str] = {}
    for key in source:
        if key.startswith(_BROKER_SECRET_PREFIXES):
            continue
        sanitized[key] = source[key]
    return sanitized
