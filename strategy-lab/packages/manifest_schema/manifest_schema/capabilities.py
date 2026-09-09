"""Pure fail-closed compatibility check for revision 1.2 manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConsumerCapabilities:
    execution_semantics: frozenset[str]
    products: frozenset[str]
    timeframes: frozenset[str]
    max_warmup_candles: int
    tick_volume: bool


def assess_manifest_capabilities(
    manifest: Any,
    capabilities: ConsumerCapabilities,
) -> tuple[bool, str]:
    """Return a stable refusal reason before any strategy can become executable."""

    semantics = getattr(manifest, "execution_semantics_version", None)
    if semantics is None:
        return True, "MANIFEST_LEGACY_CONTRACT"
    if semantics not in capabilities.execution_semantics:
        return False, "MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED"

    for entry in manifest.strategies:
        if entry.status == "rejected":
            continue
        required = entry.capabilities
        if required is None:
            return False, "MANIFEST_CAPABILITIES_MISSING"
        if required.product not in capabilities.products:
            return False, "MANIFEST_PRODUCT_UNSUPPORTED"
        if entry.timeframe not in capabilities.timeframes:
            return False, "MANIFEST_TIMEFRAME_UNSUPPORTED"
        if entry.warmup_required is None:
            return False, "MANIFEST_WARMUP_REQUIRED"
        if entry.warmup_required > capabilities.max_warmup_candles:
            return False, "MANIFEST_WARMUP_CAPACITY_EXCEEDED"
        if required.tick_volume and not capabilities.tick_volume:
            return False, "MANIFEST_TICK_VOLUME_UNAVAILABLE"
    return True, "MANIFEST_CAPABILITIES_SATISFIED"
