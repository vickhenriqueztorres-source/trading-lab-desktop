"""Anonymous batch uploader for settled trade outcomes (R-BOT-10)."""

from __future__ import annotations

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
    ) -> None:
        self._writer = writer
        self._identity_file = identity_file
        self._endpoint_url = endpoint_url
        self._upload_interval_seconds = upload_interval_seconds
        self._http_post_fn = http_post_fn or self._default_http_post
        self._token_provider = token_provider or self._default_token_provider

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

    def _load_or_create_identity(self) -> tuple[str, str]:
        """Load or create persistent anonymous UUIDv4 client identity."""
        if self._identity_file.exists():
            try:
                data = json.loads(self._identity_file.read_text(encoding="utf-8"))
                cid = str(data.get("client_id", ""))
                tok = str(data.get("client_token", ""))
                if cid and tok:
                    return cid, tok
            except Exception as exc:
                logger.warning("Failed to parse identity file %s: %s", self._identity_file, exc)

        # Generate new anonymous identity
        cid = str(uuid.uuid4())
        tok = self._token_provider(cid)
        self._identity_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._identity_file.with_suffix(".tmp")
        identity_data = {
            "client_id": cid,
            "client_token": tok,
            "created_at": datetime.now(UTC).isoformat(),
        }
        tmp_path.write_text(json.dumps(identity_data), encoding="utf-8")
        os.replace(tmp_path, self._identity_file)
        return cid, tok

    def _default_token_provider(self, client_id: str) -> str:
        """Create a default anonymous client bearer token."""
        return f"anon_jwt_{client_id}"

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

    def pending_count(self) -> int:
        """Return count of pending outcomes in local SQLite queue."""
        try:
            return self._writer.count_pending_outcomes()
        except Exception:
            return 0

    def flush_once(self, batch_size: int = 500) -> int:
        """Synchronously upload one batch of pending outcomes (fail-silent)."""
        if not self._endpoint_url:
            return 0

        try:
            rows = self._writer.fetch_pending_outcomes(limit=batch_size)
        except Exception as exc:
            logger.warning("Failed to fetch pending outcomes: %s", exc)
            return 0

        if not rows:
            return 0

        items = []
        ids_to_ack = []
        for r in rows:
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
        body = json.dumps(items).encode("utf-8")

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
