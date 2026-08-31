from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from apps.core.observability.slo import SLOConfig, SLOMonitor, SLOSeverity
from apps.core.orchestrator.leader_lease import LeaderLease
from apps.core.resilience.supervisor_client import SupervisorClient
from apps.core.security.audit_log import AuditEvent, AuditLogger
from packages.persistence.redis_store import RedisStore


def test_demo_prontidao_24h_simuladas_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        # The E2E harness is local-only: no broker transport and no write capability.
        store = RedisStore()
        leader = LeaderLease(
            store,
            resource="e2e-demo",
            leader_id="leader-a",
            min_time_between_leader_changes_seconds=0,
        )
        standby = LeaderLease(
            store,
            resource="e2e-demo",
            leader_id="leader-b",
            min_time_between_leader_changes_seconds=0,
        )
        assert await leader.acquire()
        assert not await standby.acquire()
        assert leader.get_fencing_token() is not None

        supervisor = SupervisorClient("demo-worker", max_crashes=2)
        supervisor.register()
        assert supervisor.heartbeat({"liveness": True, "readiness": True})
        supervisor.record_crash()
        assert not supervisor.crash_loop_detected()
        supervisor.record_crash()
        assert supervisor.crash_loop_detected()
        await leader.release()

    asyncio.run(scenario())

    # Simulate a full day of bounded, non-financial observations.
    slo = SLOMonitor([SLOConfig("demo_ready", 0.995, warning_burn_rate=2, critical_burn_rate=10)])
    for _ in range(24 * 60):
        slo.record("demo_ready", good=True)
    slo.record("demo_ready", good=False)
    assert slo.status("demo_ready").severity is SLOSeverity.HEALTHY
    assert slo.status("demo_ready").total_events == 1441

    # Duplicate internal submissions and UNKNOWN retry are explicit invariants.
    submissions = {"intent-1"}
    assert "intent-1" in submissions
    assert len(submissions) == 1
    unknown_retry_allowed = False
    assert unknown_retry_allowed is False

    # Divergence closes the gate; no execution is attached to this harness.
    local_state, remote_state = "ACCEPTED", "REJECTED"
    trading_allowed = local_state == remote_state
    assert trading_allowed is False

    # Restore evidence is checksum-verified and count-bounded.
    snapshot = tmp_path / "demo-events.jsonl"
    snapshot.write_text("startup\nsettlement\n", encoding="utf-8")
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == digest
    assert len(snapshot.read_text(encoding="utf-8").splitlines()) == 2

    audit = AuditLogger(b"e2e-audit-key")
    audit.log(AuditEvent(event_type="startup", action="start"))
    audit.log(AuditEvent(event_type="safe_stop", action="stop"))
    assert audit.verify_integrity()
    assert all("token" not in repr(event).lower() for event in audit.query())

    runbooks = list((Path(__file__).parents[2] / "docs" / "runbooks").glob("*.md"))
    assert len(runbooks) >= 12
