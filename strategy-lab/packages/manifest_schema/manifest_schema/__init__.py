"""R-MAN-1..7: local Lab implementation; never imported by the desktop bot."""

from manifest_schema.models import Management, Manifest, StrategyEntry, Validated
from manifest_schema.signing import sign, verify

__all__ = ["Management", "Manifest", "StrategyEntry", "Validated", "sign", "verify"]
