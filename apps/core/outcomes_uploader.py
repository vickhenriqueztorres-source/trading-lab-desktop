"""Anonymous batch uploader for settled trade outcomes (R-BOT-10)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from packages.persistence.writer import SingleDatabaseWriter

logger = logging.getLogger("core.outcomes_uploader")

# Exact 5 allowed payload fields according to R-BOT-10
REQUIRED_OUTCOME_FIELDS = frozenset({"client_id", "strategy_key", "ts", "won", "payout_pct"})
OUTCOME_V2_FIELDS = frozenset(
    {
        "client_id",
        "event_id",
        "strategy_key",
        "recipe_revision",
        "manifest_version",
        "execution_semantics_version",
        "primitives_version",
        "asset",
        "timeframe_s",
        "product",
        "account_environment",
        "source",
        "signal_group_id",
        "ts",
        "won",
        "payout_pct",
    }
)


def format_outcome_item(
    *,
    client_id: str,
    strategy_key: str,
    ts: int,
    won: bool,
    payout_pct: Decimal | str | float | int,
) -> dict[str, Any]:
    payload = {
        "client_id": str(client_id),
        "strategy_key": str(strategy_key),
        "ts": int(ts),
        "won": bool(won),
        "payout_pct": str(payout_pct),
    }
    # Enforce strict 5-field invariant
    if frozenset(payload.keys()) != REQUIRED_OUTCOME_FIELDS:
        raise ValueError(
            f"Invalid outcome fields: expected {REQUIRED_OUTCOME_FIELDS}, got {set(payload.keys())}"
        )
    return payload


def format_outcome_v2_item(
    *,
    client_id: str,
    event_id: str,
    strategy_key: str,
    recipe_revision: int,
    manifest_version: int,
    execution_semantics_version: str,
    primitives_version: str,
    asset: str,
    timeframe_s: int,
    product: str,
    account_environment: str,
    source: str,
    signal_group_id: str,
    ts: int,
    won: bool,
    payout_pct: Decimal | str | float | int,
) -> dict[str, Any]:
    """Build the Hub v2 contract without account or credential identifiers."""
    payload: dict[str, Any] = {
        "client_id": str(client_id),
        "event_id": str(event_id),
        "strategy_key": str(strategy_key),
        "recipe_revision": int(recipe_revision),
        "manifest_version": int(manifest_version),
        "execution_semantics_version": str(execution_semantics_version),
        "primitives_version": str(primitives_version),
        "asset": str(asset),
        "timeframe_s": int(timeframe_s),
        "product": str(product),
        "account_environment": str(account_environment),
        "source": str(source),
        "signal_group_id": str(signal_group_id),
        "ts": int(ts),
        "won": bool(won),
        "payout_pct": str(payout_pct),
    }
    if frozenset(payload) != OUTCOME_V2_FIELDS:
        raise ValueError("Invalid v2 outcome fields")
    return payload


def deterministic_event_id(order_id: str) -> str:
    """Stable UUID for a terminal order, making retries idempotent."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"trading-lab:terminal:{order_id}"))


def signal_group_id(strategy_key: str, asset: str, timeframe_s: int, ts: int) -> str:
    digest = hashlib.sha256(f"{strategy_key}|{asset}|{timeframe_s}|{ts}".encode()).hexdigest()
    return f"sha256:{digest}"


class OutcomesUploader:
    """Manages asynchronous anonymous batch uploading of trade outcomes.

    - Thread runs every upload_interval_seconds (default 300 s).
    - Uses persistent anonymous client_id (UUIDv4) and JWT token.
    - Fail-silent on network errors with backoff: preserves queue, never raises to Core.
    - Operates strictly outside the critical evaluation path.
    """

    def __init__(
        self,
        *,
        writer: SingleDatabaseWriter,
        identity_file: Path,
        endpoint_url: str = "",
        upload_interval_seconds: float = 300.0,
        http_post_fn: Callable[[str, dict[str, str], bytes], int] | None = None,
        token_provider: Callable[[str], str] | None = None,
        opt_in: bool = True,
    ) -> None:
        self._writer = writer
        self._identity_file = identity_file
        self._endpoint_url = endpoint_url
        self._upload_interval_seconds = upload_interval_seconds
        self._http_post_fn = http_post_fn or self._default_http_post
        self._token_provider = token_provider or self._default_token_provider
        self._opt_in = opt_in
        self._expired_count = 0

        self._client_id, self._client_token = self._load_or_create_identity()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._backoff_seconds = 5.0
        self._max_backoff = 300.0

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def client_token(self) -> str:
        return self._client_token

    @property
    def opt_in(self) -> bool:
        return self._opt_in

    @property
    def expired_count(self) -> int:
        return self._expired_count

    def _load_or_create_identity(self) -> tuple[str, str]:
        """Load or create persistent anonymous UUIDv4 client identity."""
        if self._identity_file.exists():
            try:
                data = json.loads(self._identity_file.read_text(encoding="utf-8"))
                cid = str(data.get("client_id", ""))
                if cid:
                    # Tokens are session secrets and are intentionally not read
                    # or retained from the identity file.
                    self._identity_file.write_text(
                        json.dumps({"client_id": cid, "created_at": data.get("created_at", "")}),
                        encoding="utf-8",
                    )
                    return cid, ""
            except Exception as exc:
                logger.warning("Failed to parse identity file %s: %s", self._identity_file, exc)

        # Generate new anonymous identity
        cid = str(uuid.uuid4())
        tok = ""
        self._identity_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._identity_file.with_suffix(".tmp")
        identity_data = {
            "client_id": cid,
            "created_at": datetime.now(UTC).isoformat(),
        }
        tmp_path.write_text(json.dumps(identity_data), encoding="utf-8")
        os.replace(tmp_path, self._identity_file)
        return cid, tok

    def _default_token_provider(self, client_id: str) -> str:
        """Obtain a short-lived anonymous Hub token; never persist it on disk."""
        if not self._endpoint_url:
            # Compatibility for offline/unit-test instances with no Hub endpoint.
            return f"anon_jwt_{client_id}"
        import urllib.request

        token_url = self._endpoint_url.rsplit("/", 1)[0] + "/client_token"
        request = urllib.request.Request(
            token_url,
            data=json.dumps({"client_id": client_id}, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read(16 * 1024).decode("utf-8"))
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("OUTCOMES_CLIENT_TOKEN_INVALID")
        return token

    def _default_http_post(self, url: str, headers: dict[str, str], data: bytes) -> int:
        """Default HTTP POST transport using urllib."""
        import urllib.request

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return int(resp.status)

    def enqueue(
        self,
        strategy_key: str,
        ts: int,
        won: bool,
        payout_pct: Decimal | str | float | int,
    ) -> None:
        """Enqueue outcome in local SQLite queue (called on settlement)."""
        try:
            now_iso = datetime.now(UTC).isoformat()
            self._writer.enqueue_outcome(
                strategy_key=strategy_key,
                ts=ts,
                won=won,
                payout_pct=str(payout_pct),
                created_at=now_iso,
            )
        except Exception as exc:
            # Fail-silent to never break settlement or core execution
            logger.warning("Failed to enqueue outcome for %s: %s", strategy_key, exc)

    def enqueue_terminal_v2(
        self,
        *,
        order_id: str,
        strategy_key: str,
        recipe_revision: int,
        manifest_version: int,
        execution_semantics_version: str,
        primitives_version: str,
        asset: str,
        timeframe_s: int,
        product: str,
        account_environment: str,
        ts: int,
        won: bool,
        payout_pct: Decimal | str | float | int,
    ) -> bool:
        """Queue one already-persisted terminal event; no financial side effect."""
        try:
            self._writer.enqueue_outcome_v2(
                event_id=deterministic_event_id(order_id),
                strategy_key=strategy_key,
                recipe_revision=recipe_revision,
                manifest_version=manifest_version,
                execution_semantics_version=execution_semantics_version,
                primitives_version=primitives_version,
                asset=asset,
                timeframe_s=timeframe_s,
                product=product,
                account_environment=account_environment,
                source="desktop_bot",
                signal_group_id=signal_group_id(strategy_key, asset, timeframe_s, ts),
                ts=ts,
                won=won,
                payout_pct=str(payout_pct),
                created_at=datetime.now(UTC).isoformat(),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to enqueue terminal outcome: %s", type(exc).__name__)
            return False

    def pending_count(self) -> int:
        """Return count of pending outcomes in local SQLite queue."""
        try:
            return self._writer.count_pending_outcomes()
        except Exception:
            return 0

    def flush_once(self, batch_size: int = 500) -> int:
        """Synchronously upload one batch of pending outcomes (fail-silent)."""
        if not self._opt_in or not self._endpoint_url:
            return 0

        # Hub accepts only the last seven days. Expiry is explicit and bounded;
        # it never touches authoritative orders or settlement records.
        try:
            expired = self._writer.expire_outcomes(int(datetime.now(UTC).timestamp()) - 7 * 86400)
            self._expired_count += expired
        except Exception:
            pass

        try:
            rows = self._writer.fetch_pending_outcomes(limit=batch_size)
        except Exception as exc:
            logger.warning("Failed to fetch pending outcomes: %s", exc)
            return 0

        if not rows:
            return 0

        # Keep the legacy five-field compatibility path usable with injected
        # transports in existing installations; v2 always obtains a real Hub token.
        v2 = rows[0].get("event_id") is not None
        if not self._client_token and not v2:
            self._client_token = f"legacy_{self._client_id}"
        if not self._client_token and v2:
            try:
                self._client_token = self._token_provider(self._client_id)
            except Exception as exc:
                logger.warning("Outcome token unavailable: %s", type(exc).__name__)
                self._apply_backoff()
                return 0

        # Keep schema versions in separate requests; the Hub rejects mixed payloads.
        rows = [row for row in rows if (row.get("event_id") is not None) == v2]
        items = []
        ids_to_ack = []
        for r in rows:
            if v2:
                items.append(
                    format_outcome_v2_item(
                        client_id=self._client_id,
                        event_id=str(r["event_id"]),
                        strategy_key=r["strategy_key"],
                        recipe_revision=int(r["recipe_revision"]),
                        manifest_version=int(r["manifest_version"]),
                        execution_semantics_version=str(r["execution_semantics_version"]),
                        primitives_version=str(r["primitives_version"]),
                        asset=str(r["asset"]),
                        timeframe_s=int(r["timeframe_s"]),
                        product=str(r["product"]),
                        account_environment=str(r["account_environment"]),
                        source=str(r["source"]),
                        signal_group_id=str(r["signal_group_id"]),
                        ts=r["ts"],
                        won=bool(r["won"]),
                        payout_pct=r["payout_pct"],
                    )
                )
            else:
                items.append(
                    format_outcome_item(
                        client_id=self._client_id,
                        strategy_key=r["strategy_key"],
                        ts=r["ts"],
                        won=bool(r["won"]),
                        payout_pct=r["payout_pct"],
                    )
                )
            ids_to_ack.append(r["id"])

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._client_token}",
        }
        body = json.dumps(
            {"schema_version": 2, "outcomes": items} if v2 else items,
            separators=(",", ":"),
        ).encode("utf-8")

        try:
            status_code = self._http_post_fn(self._endpoint_url, headers, body)
            if 200 <= status_code < 300:
                self._writer.ack_outcomes(ids_to_ack)
                self._backoff_seconds = 5.0
                logger.info(
                    "Successfully uploaded %d outcomes to %s",
                    len(items),
                    self._endpoint_url,
                )
                return len(items)
            logger.warning("Upload returned unexpected status code %d", status_code)
            self._apply_backoff()
            return 0
        except Exception as exc:
            # Fail-silent on network errors: preserve queue
            logger.warning("Outcomes upload network error (fail-silent): %s", exc)
            self._apply_backoff()
            return 0

    def _apply_backoff(self) -> None:
        self._backoff_seconds = min(self._backoff_seconds * 2.0, self._max_backoff)

    def start(self) -> None:
        """Start background daemon thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_loop,
            name="OutcomesUploaderThread",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal background thread to stop and wait for it."""
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        """Background upload loop."""
        while not self._stop_event.is_set():
            if self.pending_count() > 0:
                self.flush_once()
            # Wait for upload interval or stop signal
            interval = max(self._upload_interval_seconds, self._backoff_seconds)
            self._stop_event.wait(timeout=interval)
