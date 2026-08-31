from __future__ import annotations

from apps.core.security.audit_log import AuditEvent, AuditLogger


def test_audit_log_chain_and_query() -> None:
    logger = AuditLogger(b"test-only-secret")
    first = logger.log(AuditEvent(event_type="startup", action="start"))
    second = logger.log(
        AuditEvent(event_type="safe_stop", action="stop", resource_type="worker", success=True)
    )
    assert second.previous_event_hash == first.event_hash
    assert logger.verify_integrity()
    assert logger.query(event_type="safe_stop") == (second,)


def test_audit_chain_detects_tampering() -> None:
    logger = AuditLogger(b"test-only-secret")
    logger.log(AuditEvent(event_type="startup"))
    logger._events[0] = AuditEvent(event_type="tampered")  # type: ignore[attr-defined]
    assert logger.verify_integrity() is False
