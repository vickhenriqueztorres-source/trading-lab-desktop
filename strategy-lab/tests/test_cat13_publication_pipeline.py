from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "apps/hub/supabase/migrations/0009_publication_pipeline.sql"
PUBLISH = ROOT / "apps/hub/supabase/functions/publish/index.ts"
MIRROR = ROOT / "apps/hub/supabase/functions/mirror/index.ts"
CURRENT = ROOT / "apps/hub/supabase/functions/manifest_current/index.ts"
HUB_SHARED = ROOT / "apps/hub/supabase/functions/_shared/hub.ts"
DEPLOY = ROOT / "scripts/supabase_apply_remote.ps1"
CONFIG = ROOT / "apps/hub/supabase/config.toml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cat13_migration_serializes_publication_and_keeps_authoritative_pointer() -> None:
    sql = _read(MIGRATION)

    assert "create table if not exists public.publication_journal" in sql
    assert "create table if not exists public.manifest_pointers" in sql
    assert "create table if not exists public.manifest_mirror_outbox" in sql
    assert "perform pg_advisory_xact_lock(hashtext('strategy-lab-manifest:' || p_channel))" in sql
    assert "perform pg_advisory_xact_lock(hashtext('strategy-lab-manifest-commit'))" in sql
    assert "unique (channel, manifest_version)" in sql
    assert "check (storage_path ~ '^v[1-9][0-9]*[.]json$')" in sql
    assert "where public.manifest_pointers.manifest_version < excluded.manifest_version" in sql
    assert "attempts between 0 and 5" in sql


def test_cat13_production_publish_requires_sealed_real_market_evidence() -> None:
    sql = _read(MIGRATION)
    publish = _read(PUBLISH)

    for expected in (
        "HUB_PRODUCTION_EVIDENCE_REQUIRED",
        "HUB_RESEARCH_RUN_NOT_SEALED",
        "HUB_DATASET_EVIDENCE_INCOMPATIBLE",
        "HUB_APPROVED_RESEARCH_EVIDENCE_REQUIRED",
        "HUB_PORTFOLIO_EVIDENCE_REQUIRED",
    ):
        assert expected in sql
    assert 'schema_revision !== "1.2"' in publish
    assert 'dataset?.kind !== "real_market"' in publish


def test_cat13_publish_uses_immutable_version_objects_and_repairable_current_projection() -> None:
    publish = _read(PUBLISH)
    shared = _read(HUB_SHARED)

    assert "createImmutableManifest(path, body" in publish
    assert "downloadManifest(path)" in publish
    assert "repairLegacyCurrent" in publish
    assert "listManifestCandidates(channel)" in publish
    assert "markProjectionSynced" in publish
    assert '"x-upsert": "false"' in shared
    assert '"x-upsert": "true"' in shared
    assert "select max(manifest_version)" not in publish.lower()


def test_cat13_mirror_is_durable_bounded_and_hash_verified() -> None:
    mirror = _read(MIRROR)
    sql = _read(MIGRATION)

    assert "claimMirrorJob" in mirror
    assert "expected_sha256" in mirror
    assert "manifestSha(body)" in mirror
    assert "manifestSha(mirrored)" in mirror
    assert "completeMirrorJob" in mirror
    assert "failMirrorJob" in mirror
    assert "attempts < 5" in sql
    assert "least(" in sql and "power(2::numeric" in sql


def test_cat13_manifest_current_endpoint_is_public_cacheable_and_last_good() -> None:
    current = _read(CURRENT)
    config = _read(CONFIG)

    assert "handleManifestCurrent" in current
    assert "listManifestCandidates" in current
    assert '"cache-control": "public,max-age=60,stale-if-error=86400"' in current
    assert '"x-manifest-fallback"' in current
    assert '"if-none-match"' in current
    assert "[functions.manifest_current]" in config
    assert "verify_jwt = false" in config


def test_cat13_remote_apply_deploys_manifest_current_with_custom_auth() -> None:
    deploy = _read(DEPLOY)

    assert "archive mirror publish" in deploy
    assert "client_token outcomes manifest_current" in deploy
    assert "--no-verify-jwt" in deploy
    assert (
        "SUPABASE_STAGING_DB_URL must be a percent-encoded PostgreSQL connection string" in deploy
    )
    assert "Refusing to apply Strategy Lab Hub changes against the production ref" in deploy
