from __future__ import annotations

import base64
import re

from packages.licensing.models import SignedLease


def encode_product_key(signed_lease: SignedLease, prefix: str = "TLKEY-PRO-") -> str:
    """Encode a SignedLease into a human-distributable product key string."""
    raw_bytes = signed_lease.to_bytes()
    b64_part = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    return f"{prefix}{b64_part}"


def decode_product_key(text: str) -> SignedLease:
    """Decode a product key string, raw base64, or JSON .lic payload into a SignedLease.

    Raises ValueError if the input is malformed.
    """
    clean = text.strip()
    if not clean:
        raise ValueError("Product key cannot be empty")

    # 1. Check if it's direct JSON (e.g. read from a .lic file)
    if clean.startswith("{") and clean.endswith("}"):
        try:
            return SignedLease.from_bytes(clean.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON license format: {exc}") from exc

    # 2. Check if it starts with TLKEY prefix (case-insensitive)
    upper = clean.upper()
    b64_part = clean
    for prefix in ("TLKEY-PRO-", "TLKEY-DEMO-", "TLKEY-REAL-", "TLKEY-"):
        if upper.startswith(prefix):
            b64_part = clean[len(prefix) :].strip()
            break

    # Strip any extra spaces, linebreaks, or carriage returns that might have been pasted
    b64_part = re.sub(r"\s+", "", b64_part)

    # Re-pad base64
    missing_padding = len(b64_part) % 4
    if missing_padding:
        b64_part += "=" * (4 - missing_padding)

    try:
        raw_bytes = base64.urlsafe_b64decode(b64_part.encode("ascii"))
    except Exception:
        # Also try standard b64 if urlsafe failed
        try:
            raw_bytes = base64.b64decode(b64_part.encode("ascii"))
        except Exception as exc:
            raise ValueError("Invalid product key encoding") from exc

    try:
        return SignedLease.from_bytes(raw_bytes)
    except Exception as exc:
        raise ValueError(f"Invalid product key payload: {exc}") from exc
