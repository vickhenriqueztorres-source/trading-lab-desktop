from __future__ import annotations

import base64
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.identity.models import DeviceIdentity
from packages.security import SecretValue, UserScopedVault

_DEVICE_ID_KEY = "identity.device_id"
_DEVICE_PRIVATE_KEY = "identity.device_private_key"


class DeviceIdentityManager:
    def __init__(self, vault: UserScopedVault) -> None:
        self._vault = vault

    def load_or_create(self) -> DeviceIdentity:
        stored_id = self._vault.load(_DEVICE_ID_KEY)
        stored_private = self._vault.load(_DEVICE_PRIVATE_KEY)
        if (stored_id is None) != (stored_private is None):
            raise RuntimeError("device identity is incomplete")
        if stored_id is None:
            private_key = Ed25519PrivateKey.generate()
            private_raw = private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            device_id = str(uuid4())
            self._vault.store(_DEVICE_ID_KEY, SecretValue.from_text(device_id))
            self._vault.store(_DEVICE_PRIVATE_KEY, SecretValue(private_raw))
        else:
            assert stored_private is not None
            device_id = stored_id.reveal_text()
            private_key = Ed25519PrivateKey.from_private_bytes(stored_private.reveal_bytes())
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return DeviceIdentity(
            device_id=device_id,
            public_key_b64=base64.urlsafe_b64encode(public_raw).decode("ascii"),
        )

    def sign(self, message: bytes) -> bytes:
        private_value = self._vault.load(_DEVICE_PRIVATE_KEY)
        if private_value is None:
            raise RuntimeError("device identity has not been created")
        private_key = Ed25519PrivateKey.from_private_bytes(private_value.reveal_bytes())
        return private_key.sign(message)
