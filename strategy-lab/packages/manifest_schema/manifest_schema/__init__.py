"""R-MAN-1..7: local Lab implementation; never imported by the desktop bot."""

from manifest_schema.capabilities import ConsumerCapabilities, assess_manifest_capabilities
from manifest_schema.models import (
    Composition,
    DatasetEvidence,
    Management,
    Manifest,
    RequiredCapabilities,
    StrategyEntry,
    TelemetryPolicy,
    Validated,
)
from manifest_schema.signing import sign, verify

__all__ = [
    "Composition",
    "ConsumerCapabilities",
    "DatasetEvidence",
    "Management",
    "Manifest",
    "RequiredCapabilities",
    "StrategyEntry",
    "TelemetryPolicy",
    "Validated",
    "assess_manifest_capabilities",
    "sign",
    "verify",
]
