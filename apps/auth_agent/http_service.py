from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any

from apps.auth_agent.fake_service import (
    FakeIdentityServiceError,
    FakeIdentityServiceErrorCode,
    IdentityServiceError,
    IdentityServiceErrorCode,
)
from apps.auth_agent.pinned_keys import PINNED_LEASE_KEYS
from packages.identity import OtpCode

__all__ = [
    "HttpIdentityService",
    "IdentityServiceError",
    "IdentityServiceErrorCode",
]


def _decode_b64_key(raw_b64: str) -> bytes:
    normalized = raw_b64.strip()
    rem = len(normalized) % 4
    if rem > 0:
        normalized += "=" * (4 - rem)
    key_bytes = base64.urlsafe_b64decode(normalized)
    if len(key_bytes) != 32:
        raise ValueError(f"Invalid Ed25519 public key length: {len(key_bytes)}")
    return key_bytes


class HttpIdentityService:
    """HTTP client implementation of the identity and licensing service port."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url cannot be empty")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._keys_cache: dict[str, bytes] = {}
        self._keys_cached_at: float = 0.0

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        token: str | None = None,
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "TradingLab-Desktop/2.0",
        }
        encoded_data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded_data = json.dumps(body).encode("utf-8")
        elif method.upper() in ("POST", "PUT", "PATCH"):
            headers["Content-Type"] = "application/json"
            encoded_data = b"{}"

        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(
            url,
            data=encoded_data,
            headers=headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = getattr(resp, "status", 200)
                if status == 204:
                    return None
                resp_bytes = resp.read()
                if not resp_bytes:
                    return None
                try:
                    return json.loads(resp_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    text = resp_bytes.decode("utf-8").strip()
                    if text.lower() == "true":
                        return True
                    if text.lower() == "false":
                        return False
                    return text
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE) from exc

    def _handle_http_error(self, exc: urllib.error.HTTPError) -> None:
        try:
            raw_body = exc.read().decode("utf-8")
            data = json.loads(raw_body)
        except Exception:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE) from exc

        code_str: str | None = None
        if isinstance(data, dict):
            error_val = data.get("error")
            if isinstance(error_val, dict):
                code_str = str(error_val.get("code") or "")
            elif isinstance(error_val, str):
                code_str = error_val
            elif "code" in data:
                code_str = str(data.get("code") or "")
            elif "detail" in data and isinstance(data["detail"], str):
                code_str = data["detail"]

        if not code_str:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE) from exc

        resolved = self._resolve_error_code(code_str)
        raise FakeIdentityServiceError(resolved)

    @staticmethod
    def _resolve_error_code(code_str: str) -> FakeIdentityServiceErrorCode:
        try:
            return FakeIdentityServiceErrorCode(code_str)
        except ValueError:
            pass
        if not code_str.startswith("AUTH_"):
            try:
                return FakeIdentityServiceErrorCode(f"AUTH_{code_str}")
            except ValueError:
                pass
        return FakeIdentityServiceErrorCode.UNAVAILABLE

    @property
    def lease_verification_keys(self) -> dict[str, bytes]:
        pinned: dict[str, bytes] = {}
        for key_id, b64_val in PINNED_LEASE_KEYS.items():
            try:
                pinned[key_id] = _decode_b64_key(b64_val)
            except Exception:
                continue

        now = time.monotonic()
        if self._keys_cache and (now - self._keys_cached_at) < 3600.0:
            result = dict(self._keys_cache)
            result.update(pinned)
            return result

        fetched: dict[str, bytes] = {}
        try:
            data = self._request("GET", "/.well-known/lease-keys")
            keys_dict: dict[str, Any] = {}
            if isinstance(data, dict):
                if "keys" in data and isinstance(data["keys"], dict):
                    keys_dict = data["keys"]
                elif "keys" in data and isinstance(data["keys"], list):
                    for item in data["keys"]:
                        if isinstance(item, dict) and "key_id" in item and "public_key_b64" in item:
                            keys_dict[str(item["key_id"])] = item["public_key_b64"]
                else:
                    keys_dict = data

            for key_id, b64_val in keys_dict.items():
                if isinstance(b64_val, str):
                    try:
                        fetched[str(key_id)] = _decode_b64_key(b64_val)
                    except Exception:
                        continue
            self._keys_cache = fetched
            self._keys_cached_at = now
        except Exception:
            pass

        result = dict(self._keys_cache)
        result.update(pinned)
        return result

    def start_login(self, email: str, pkce_challenge: str) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/v1/auth/start",
            body={"email": email, "pkce_challenge": pkce_challenge},
        )
        if not isinstance(response, dict):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)
        return response

    def complete_login(
        self,
        challenge_id: str,
        code: OtpCode,
        pkce_verifier: str,
    ) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/v1/auth/verify",
            body={
                "challenge_id": challenge_id,
                "otp_code": code.value,
                "pkce_verifier": pkce_verifier,
            },
        )
        if not isinstance(response, dict):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)
        return response

    def refresh_session(self, refresh_token: str) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/v1/auth/refresh",
            body={"refresh_token": refresh_token},
        )
        if not isinstance(response, dict):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)
        return response

    def register_device(
        self,
        access_token: str,
        device_id: str,
        public_key_b64: str,
    ) -> None:
        self._request(
            "POST",
            "/api/v1/device/register",
            body={"device_id": device_id, "public_key_b64": public_key_b64},
            token=access_token,
        )

    def create_device_challenge(self, access_token: str, device_id: str) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/v1/device/challenge",
            body={"device_id": device_id},
            token=access_token,
        )
        if not isinstance(response, dict):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)
        return response

    def issue_lease(
        self,
        access_token: str,
        device_id: str,
        challenge_id: str,
        signature_b64: str,
    ) -> dict[str, object]:
        response = self._request(
            "POST",
            "/api/v1/lease/issue",
            body={
                "device_id": device_id,
                "challenge_id": challenge_id,
                "signature_b64": signature_b64,
            },
            token=access_token,
        )
        if not isinstance(response, dict):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)
        return response

    def is_lease_revoked(self, lease_id: str) -> bool:
        response = self._request("GET", f"/api/v1/lease/revoked/{lease_id}")
        if isinstance(response, dict):
            return bool(response.get("revoked", False))
        if isinstance(response, bool):
            return response
        if isinstance(response, str):
            return response.strip().lower() in ("true", "1")
        return bool(response)
