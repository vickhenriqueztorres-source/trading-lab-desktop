"""Unit test verifying that LeaseSigner output is accepted by desktop LeaseVerifier."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from apps.license_server.keys import get_signer, load_signing_key, public_keys
from apps.license_server.settings import Settings
from packages.licensing.lease import LeaseVerifier
from packages.licensing.models import LeaseClaims


def test_lease_signer_produces_valid_signed_lease_accepted_by_lease_verifier(
    test_settings: Settings,
) -> None:
    """LeaseSigner canonical bytes signature must be strictly verified by desktop LeaseVerifier."""
    load_signing_key(test_settings)
    signer = get_signer()
    pub_dict = public_keys()
    assert signer.key_id in pub_dict

    pub_b64 = pub_dict[signer.key_id]
    pub_bytes = base64.urlsafe_b64decode(pub_b64)
    assert len(pub_bytes) == 32

    # Instantiate the desktop LeaseVerifier
    verifier = LeaseVerifier({signer.key_id: pub_bytes})

    now = datetime.now(UTC)
    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id=str(uuid4()),
        device_id="dev-test-12345",
        issued_at=now,
        expires_at=now + timedelta(days=2),
        plan="PHASE0_PRACTICE",
        broker_access=("DERIV", "IQ_OPTION"),
        strategy_packs=("core", "tail-probability-edge"),
        real_mode_allowed=False,
        client_version_min="0.0.1",
        client_version_max="99.0.0",
        nonce=secrets.token_urlsafe(18),
    )

    signed_lease = signer.sign(claims)

    # Verifier must decode and return identical claims
    verified_claims = verifier.verify(signed_lease)

    assert verified_claims.format_version == claims.format_version
    assert verified_claims.lease_id == claims.lease_id
    assert verified_claims.user_id == claims.user_id
    assert verified_claims.device_id == claims.device_id
    assert verified_claims.issued_at == claims.issued_at
    assert verified_claims.expires_at == claims.expires_at
    assert verified_claims.plan == claims.plan
    assert verified_claims.broker_access == claims.broker_access
    assert verified_claims.strategy_packs == claims.strategy_packs
    assert verified_claims.real_mode_allowed is False
    assert verified_claims.nonce == claims.nonce
