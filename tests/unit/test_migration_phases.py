from __future__ import annotations

from packages.persistence.migrations import Migration, MigrationPhase, SchemaMigrator


def test_schema_migrator_runs_each_phase_once() -> None:
    calls: list[str] = []
    migration = Migration(
        1,
        "test",
        (),
        expand_fn=lambda _connection: calls.append("expand"),
        migrate_fn=lambda _connection: calls.append("migrate"),
        contract_fn=lambda _connection: calls.append("contract"),
    )
    migrator = SchemaMigrator(migrations=(migration,))
    assert migrator.migrate_to(1, MigrationPhase.EXPAND) == 1
    assert migrator.migrate_to(1, MigrationPhase.EXPAND) == 1
    assert migrator.migrate_to(1, MigrationPhase.MIGRATE) == 1
    assert migrator.migrate_to(1, MigrationPhase.CONTRACT) == 1
    assert calls == ["expand", "migrate", "contract"]
