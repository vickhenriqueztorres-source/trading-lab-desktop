"""Strategy Lab CLI entry point; no connection on import/help."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from strategy_lab.archive import OperationalArchiveRepository
from strategy_lab.collect.backup import run_backup
from strategy_lab.collect.canary import CanaryMismatch
from strategy_lab.collect.clock import Clock, ClockError
from strategy_lab.collect.credentials import (
    keyring_credentials_available,
    prompt_and_store_credentials,
)
from strategy_lab.collect.iq_client import (
    LAB_ROOT,
    FakeIQClient,
    IQClient,
    IQClientError,
    IQClientProtocol,
)
from strategy_lab.collect.pg_repository import PostgresRepository
from strategy_lab.collect.preflight import collection_preflight
from strategy_lab.collect.recorded_canary import DEFAULT_RECORDED_CANARY, RecordedCanaryError
from strategy_lab.collect.recorder import record_fixture
from strategy_lab.collect.repository import FakeRepository, RepositoryError
from strategy_lab.collect.runner import fake_fixture_path, run_collect, status_report, to_json
from strategy_lab.research.dataset import ResearchDataset, coverage_report
from strategy_lab.research.grammar import DEFAULT_TRIAL_BUDGET
from strategy_lab.retention import (
    FakeInventoryRepository,
    InventoryRepository,
    build_cleanup_plan,
    inventory_json,
    plan_from_json,
    plan_json,
    quota_status,
)

SAFE_REASON_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,79}", re.ASCII)


def safe_reason(error: Exception) -> str:
    """Expose only stable machine codes, never an upstream/driver message."""
    reason = str(error)
    return reason if SAFE_REASON_PATTERN.fullmatch(reason) else "COLLECT_ABORTED"


def parse_epoch(text: str) -> int:
    try:
        if text.isascii() and text.isdigit():
            return int(text)
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if instant.utcoffset() is None or instant.microsecond:
            raise ValueError
        return int(instant.astimezone(UTC).timestamp())
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError("Use epoch inteiro ou ISO-8601 com timezone.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strategy-lab")
    subcommands = parser.add_subparsers(dest="command", required=True)
    record = subcommands.add_parser(
        "record-fixture", help="Coleta manual, somente preços, até 1000 M1."
    )
    record.add_argument("--asset", required=True)
    record.add_argument("--from", dest="from_ts", required=True, type=parse_epoch)
    record.add_argument("--to", dest="to_ts", required=True, type=parse_epoch)
    record.add_argument("--output", type=Path)
    canary = subcommands.add_parser(
        "record-canary", help="Grava cinco velas reais para revisão; não grava o banco."
    )
    canary.add_argument("--asset", required=True)
    canary.add_argument("--from", dest="from_ts", required=True, type=parse_epoch)
    canary.add_argument("--output", type=Path, default=DEFAULT_RECORDED_CANARY)
    preflight = subcommands.add_parser(
        "collection-preflight", help="Verifica pré-requisitos locais sem acessar a corretora."
    )
    preflight.add_argument("--canary-file", type=Path, default=DEFAULT_RECORDED_CANARY)
    collect = subcommands.add_parser("collect", help="Executa coleta diaria do Strategy Lab.")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--payout-only", action="store_true")
    collect.add_argument("--assets", nargs="+", default=["EURUSD-OTC"])
    collect.add_argument("--from", dest="from_ts", type=parse_epoch)
    collect.add_argument("--force-source", action="store_true")
    collect.add_argument("--canary-file", type=Path, default=DEFAULT_RECORDED_CANARY)
    credentials = subcommands.add_parser(
        "credentials", help="Configura a credencial isolada de coleta no cofre do SO."
    )
    credentials.add_argument("action", choices=["status", "set"])
    status = subcommands.add_parser("status", help="Mostra saude da coleta.")
    status.add_argument("--dry-run", action="store_true")
    backup = subcommands.add_parser("backup", help="Backup criptografado do banco Strategy Lab.")
    backup.add_argument("--output-dir", type=Path)
    backup.add_argument(
        "--database-only",
        action="store_true",
        help="Exclusão explícita dos objetos frios; o padrão inclui DB e Storage.",
    )
    archive = subcommands.add_parser(
        "archive", help="Planeja arquivo frio; exige --execute para remover dados quentes."
    )
    archive.add_argument("--execute", action="store_true")
    archive.add_argument("--worker-id", default="strategy-lab-local")
    retention = subcommands.add_parser(
        "retention", help="Inventário e plano fechado de retenção (dry-run por padrão)."
    )
    retention.add_argument(
        "--dry-run", action="store_true", help="Mantém o modo somente leitura (padrão)."
    )
    retention.add_argument(
        "--execute", action="store_true", help="Aplica somente o plano fechado informado."
    )
    retention.add_argument(
        "--plan-file", type=Path, help="Plano JSON revisado para execução explícita."
    )
    retention.add_argument("--output", type=Path, help="Grava o plano JSON no dry-run.")
    retention.add_argument(
        "--project-ref", default=os.environ.get("SUPABASE_PROJECT_REF", "staging-local")
    )
    retention.add_argument("--environment", choices=["staging", "production"], default="staging")
    retention.add_argument("--confirm", default="", help="Token RETENTION-APPLY:<plan_hash>.")
    retention.add_argument("--allow-production", action="store_true", help=argparse.SUPPRESS)
    retention.add_argument("--quota-bytes", type=int, default=1_000_000_000)
    retention.add_argument("--backlog-items", type=int, default=0)
    retention.add_argument("--now", type=parse_epoch)
    research = subcommands.add_parser("research", help="Ferramentas de pesquisa offline.")
    research.add_argument("--coverage-report", action="store_true")
    research.add_argument("--synthetic", action="store_true", help="Gera candidatos sintéticos.")
    research.add_argument("--seed", type=int, default=1, help="Seed determinística para pesquisa.")
    research.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_TRIAL_BUDGET,
        help="Limite máximo de candidatos elegíveis por experimento.",
    )
    research.add_argument(
        "--active-manifest", type=Path, help="Manifesto ativo para identificar novas oportunidades."
    )
    research.add_argument("--output-dir", type=Path, default=Path("research/runs"))
    research.add_argument("--run-id", default=None)
    research.add_argument("--assets", nargs="+", default=["EURUSD-OTC"])
    research.add_argument("--from", dest="from_ts", type=parse_epoch)
    research.add_argument("--to", dest="to_ts", type=parse_epoch)
    research.add_argument("--candles-parquet")
    research.add_argument("--payouts-parquet")
    research.add_argument("--gaps-parquet")
    research.add_argument("--sessions-parquet")
    research.add_argument("--supabase", action="store_true")
    research.add_argument("--timeframe", choices=["M1", "M5", "M15"], default="M1")

    publish = subcommands.add_parser("publish", help="Publica manifesto assinado no hub.")
    publish.add_argument("--run-id", required=True, help="ID da rodada de pesquisa.")
    publish.add_argument("--key-id", required=True, choices=["A", "B"], help="ID da chave.")
    publish.add_argument("--include", nargs="+", help="Chaves de estratégias para incluir.")
    publish.add_argument("--exclude", nargs="+", help="Chaves de estratégias para excluir.")
    publish.add_argument("--promote", nargs="+", help="Chaves de estratégias para promover.")
    publish.add_argument("--candidates-file", type=Path, help="Caminho de candidates.json.")
    publish.add_argument("--keys-dir", type=Path, help="Diretório das chaves PEM.")
    publish.add_argument("--dry-run", action="store_true", help="Monta e valida sem upload.")
    publish.add_argument("--allow-test-keys", action="store_true", help="Permite chave de teste.")
    publish.add_argument("--endpoint-url", help="URL do endpoint de publicação.")
    publish.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command == "collection-preflight":
        readiness = collection_preflight(canary_path=args.canary_file, now_ts=Clock().now_ts())
        print(to_json(readiness))
        return 1 if readiness["blockers"] else 0

    if args.command == "record-canary":
        try:
            clock = Clock()
            clock.check_ntp()
            recorded = record_fixture(
                asset=args.asset,
                from_ts=args.from_ts,
                to_ts=args.from_ts + 300,
                output=args.output,
                now_ts=clock.now_ts(),
            )
        except Exception:
            print(to_json({"event": "collection_canary_record_failed", "status": "failed"}))
            return 1
        print(to_json({"event": "collection_canary_recorded", **recorded}))
        return 0

    if args.command == "credentials":
        if args.action == "status":
            print(
                to_json(
                    {
                        "event": "strategy_lab_collection_credentials_status",
                        "configured": keyring_credentials_available(),
                    }
                )
            )
            return 0
        try:
            prompt_and_store_credentials()
        except (Exception, KeyboardInterrupt):
            print(
                to_json(
                    {
                        "event": "strategy_lab_collection_credentials_failed",
                        "status": "failed",
                    }
                )
            )
            return 1
        print(
            to_json(
                {
                    "event": "strategy_lab_collection_credentials_configured",
                    "status": "ok",
                }
            )
        )
        return 0

    if args.command == "publish":
        if getattr(args, "yes", False):
            print("Erro R-PUB-3: flag --yes é proibida. Confirmação manual obrigatória.")
            return 1
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from manifest_schema.canonical import canonical_bytes
            from manifest_schema.signing import TEST_PUBLIC_KEY

            from strategy_lab.publish.builder import build_manifest, load_candidates_file
            from strategy_lab.publish.differ import (
                compute_diff,
                format_diff_report,
                prompt_confirmation,
            )
            from strategy_lab.publish.preflight import run_preflight
            from strategy_lab.publish.signer import load_private_key_bytes, sign_manifest
            from strategy_lab.publish.uploader import upload_manifest

            # 1. Load candidates
            cand_path = args.candidates_file or (
                Path("research/runs") / args.run_id / "candidates.json"
            )
            candidates_data = load_candidates_file(cand_path)

            # 2. Build manifest
            manifest = build_manifest(
                run_id=args.run_id,
                candidates_data=candidates_data,
                include_keys=args.include,
                exclude_keys=args.exclude,
                promote_keys=args.promote,
                key_id=args.key_id,
            )

            # 3. Load private key & derive public key
            priv_bytes = load_private_key_bytes(
                args.key_id, keys_dir=args.keys_dir, verify_perms=True
            )
            pub = Ed25519PrivateKey.from_private_bytes(priv_bytes).public_key().public_bytes_raw()
            public_keys: dict[str, bytes] = {args.key_id: pub}
            if args.allow_test_keys:
                public_keys["TEST"] = TEST_PUBLIC_KEY

            # 4. Sign manifest
            signed_manifest = sign_manifest(
                manifest,
                priv_bytes,
                key_id=args.key_id,
                allow_test_keys=args.allow_test_keys,
            )

            # 5. Preflight check
            run_preflight(
                signed_manifest,
                public_keys=public_keys,
                allow_test_keys=args.allow_test_keys,
            )

            # 6. Diff and confirmation
            diff = compute_diff(None, signed_manifest)
            diff_text = format_diff_report(diff)
            print(diff_text)

            if not args.dry_run:
                confirmed = prompt_confirmation(len(signed_manifest.strategies))
                if not confirmed:
                    print("Publicação cancelada pelo operador.")
                    return 1

                # 7. Upload
                payload = canonical_bytes(signed_manifest.model_dump(mode="json"))
                upload_res = upload_manifest(payload, endpoint_url=args.endpoint_url)
                print(
                    to_json(
                        {
                            "event": "manifest_published",
                            "sha256": upload_res.sha256,
                            "status": "ok",
                        }
                    )
                )
            else:
                print(
                    to_json(
                        {
                            "event": "manifest_dry_run_completed",
                            "status": "ok",
                            "version": signed_manifest.manifest_version,
                        }
                    )
                )
            return 0
        except Exception as err:
            print(
                to_json(
                    {
                        "event": "manifest_publish_failed",
                        "error": str(err),
                        "status": "failed",
                    }
                )
            )
            return 1
    if args.command == "collect":
        try:
            if not args.dry_run:
                readiness = collection_preflight(
                    canary_path=args.canary_file, now_ts=Clock().now_ts()
                )
                if readiness["blockers"]:
                    print(to_json(readiness))
                    return 1
            repository = (
                FakeRepository()
                if args.dry_run
                else PostgresRepository(force_source=args.force_source)
            )
            clock = Clock(lambda: 1700000400) if args.dry_run else Clock()
            initial_from_ts = args.from_ts
            if args.dry_run and initial_from_ts is None:
                initial_from_ts = 1700000040

            def client_factory() -> IQClientProtocol:
                if args.dry_run:
                    return FakeIQClient(fake_fixture_path(), now=clock.now_ts)
                return IQClient()

            report = run_collect(
                assets=args.assets,
                repository=repository,
                clock=clock,
                client_factory=client_factory,
                dry_run=args.dry_run,
                payout_only=args.payout_only,
                initial_from_ts=initial_from_ts,
                check_ntp=not args.dry_run,
                canary_path=None if args.dry_run else args.canary_file,
            )
        except (
            CanaryMismatch,
            ClockError,
            IQClientError,
            RecordedCanaryError,
            RepositoryError,
        ) as exc:
            print(to_json({"event": "strategy_lab_collect_failed", "reason": safe_reason(exc)}))
            return 1
        except Exception:
            print(to_json({"event": "strategy_lab_collect_failed", "status": "failed"}))
            return 1
        print(to_json(report))
        return 0
    if args.command == "status":
        repository = FakeRepository()
        print(to_json(status_report(repository, now_ts=Clock(lambda: 1700000400).now_ts())))
        return 0
    if args.command == "backup":
        try:
            target = run_backup(
                db_url=os.environ.get("SUPABASE_DB_URL", ""),
                age_recipient=os.environ.get("STRATEGY_LAB_AGE_RECIPIENT", ""),
                output_dir=args.output_dir,
            )
            cold_manifest: Path | None = None
            if not args.database_only:
                from strategy_lab.archive import backup_verified_objects, storage_from_env

                records = _archive_repository().all_verified_objects()
                if records:
                    cold_manifest = backup_verified_objects(
                        records, storage_from_env(), target.parent / target.stem
                    )
        except Exception:
            print(to_json({"event": "strategy_lab_backup_failed", "status": "failed"}))
            return 1
        print(
            to_json(
                {
                    "event": "strategy_lab_backup_completed",
                    "status": "ok",
                    "path": str(target),
                    "cold_objects_manifest": None if cold_manifest is None else str(cold_manifest),
                }
            )
        )
        return 0
    if args.command == "archive":
        try:
            from strategy_lab.archive import ArchiveExecutor, storage_from_env

            archive_repository = _archive_repository()
            planned_job = archive_repository.plan()
            if not args.execute:
                print(
                    to_json(
                        {
                            "event": "strategy_lab_archive_plan",
                            "status": "dry_run",
                            "job_id": planned_job,
                            "deletion_authorized": False,
                        }
                    )
                )
                return 0
            archive_report = ArchiveExecutor(
                archive_repository,
                storage_from_env(),
                worker_id=args.worker_id,
            ).run_once()
        except Exception:
            print(to_json({"event": "strategy_lab_archive_failed", "status": "failed"}))
            return 1
        print(to_json({"event": "strategy_lab_archive", **asdict(archive_report)}))
        return 0 if archive_report.status in {"idle", "completed"} else 1
    if args.command == "retention":
        try:
            retention_repository = _retention_repository(args.project_ref, args.environment)
            if not args.execute:
                retention_snapshot = retention_repository.snapshot()
                now_ts = args.now if args.now is not None else retention_snapshot.generated_at
                plan = build_cleanup_plan(retention_snapshot, now_ts=now_ts)
                quota = quota_status(
                    retention_snapshot,
                    quota_bytes=args.quota_bytes,
                    backlog_items=args.backlog_items,
                )
                report = inventory_json(retention_snapshot, plan, quota)
                report["status"] = "dry_run"
                report["deletion_authorized"] = False
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(
                        json.dumps(plan_json(plan), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    report["plan_file"] = str(args.output)
                print(to_json(report))
                return 0
            if args.plan_file is None:
                raise ValueError("RETENTION_PLAN_FILE_REQUIRED")
            plan = plan_from_json(json.loads(args.plan_file.read_text(encoding="utf-8")))
            from strategy_lab.retention import apply_cleanup_plan

            deleted = apply_cleanup_plan(
                plan,
                retention_repository,
                project_ref=args.project_ref,
                environment=args.environment,
                confirmation_token=args.confirm,
                allow_production=args.allow_production,
            )
            print(
                to_json(
                    {
                        "event": "strategy_lab_retention_applied",
                        "status": "ok",
                        "deleted": deleted,
                        "plan_hash": plan.plan_hash,
                    }
                )
            )
            return 0
        except Exception as exc:
            print(
                to_json(
                    {
                        "event": "strategy_lab_retention_failed",
                        "status": "failed",
                        "reason": str(exc),
                    }
                )
            )
            return 1
    if args.command == "research":
        from strategy_lab.research.dataset import timeframe_seconds

        parquet_requested = bool(
            args.candles_parquet
            or args.payouts_parquet
            or args.gaps_parquet
            or args.sessions_parquet
        )
        parquet_complete = bool(args.candles_parquet and args.payouts_parquet)
        selected_sources = (
            int(bool(args.synthetic)) + int(bool(args.supabase)) + int(parquet_requested)
        )
        if (
            selected_sources != 1
            or (parquet_requested and not parquet_complete)
            or (args.coverage_report and args.synthetic)
            or (not args.synthetic and (args.from_ts is None or args.to_ts is None))
        ):
            print(
                to_json(
                    {
                        "event": "strategy_lab_research_failed",
                        "status": "failed",
                        "reason": "RES_DATASET_SOURCE_REQUIRED",
                    }
                )
            )
            return 1
        tf_s = timeframe_seconds(args.timeframe)
        if args.coverage_report:
            try:
                dataset = (
                    ResearchDataset.from_parquet(
                        args.candles_parquet,
                        args.payouts_parquet,
                        args.gaps_parquet,
                        sessions_path=args.sessions_parquet,
                    )
                    if parquet_complete
                    else _dataset_from_supabase(args.assets, args.from_ts, args.to_ts)
                )
                coverage_entries = coverage_report(
                    dataset,
                    args.assets,
                    args.from_ts,
                    args.to_ts,
                    timeframe_s=tf_s,
                )
            except Exception:
                print(to_json({"event": "strategy_lab_research_failed", "status": "failed"}))
                return 1
            print(to_json({"event": "strategy_lab_coverage_report", "assets": coverage_entries}))
            return 0 if all(bool(item["accepted"]) for item in coverage_entries) else 1

        from decimal import Decimal

        from strategy_lab.research.dataset import synthetic_snapshot
        from strategy_lab.research.grammar import enumerate_candidates
        from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
        from strategy_lab.research.runner import run_research_pipeline
        from strategy_lab.research.synthetic import (
            BASE_TS,
            edge_series,
            make_injected_edge_candidate,
            register_synthetic_primitives,
        )

        run_id = args.run_id or f"run_{args.seed}_{int(datetime.now(tz=UTC).timestamp())}"

        active_keys: set[str] = set()
        if args.active_manifest and args.active_manifest.exists():
            manifest_data = json.loads(args.active_manifest.read_text(encoding="utf-8"))
            active_keys = {s["key"] for s in manifest_data.get("strategies", []) if "key" in s}

        if not args.synthetic:
            if len(args.assets) != 1:
                print(
                    to_json(
                        {
                            "event": "strategy_lab_research_failed",
                            "status": "failed",
                            "reason": "RES_MULTI_ASSET_REQUIRES_PARTITIONED_RUNS",
                        }
                    )
                )
                return 1
            dataset = (
                ResearchDataset.from_parquet(
                    args.candles_parquet,
                    args.payouts_parquet,
                    args.gaps_parquet,
                    sessions_path=args.sessions_parquet,
                )
                if parquet_complete
                else _dataset_from_supabase(args.assets, args.from_ts, args.to_ts)
            )
            bundle = dataset.bundle_for(
                args.assets[0],
                tf_s,
                args.from_ts,
                args.to_ts,
                now_ts=int(datetime.now(tz=UTC).timestamp()),
            )
            payout_lookup = PayoutLookup.from_rows(dataset.payouts.to_dicts())
            out_dir = args.output_dir / run_id
            res = run_research_pipeline(
                list(bundle.candles),
                payout_lookup,
                run_id=run_id,
                assets=args.assets,
                seed=args.seed,
                max_candidates=args.max_candidates,
                output_dir=out_dir,
                dataset=dataset,
                dataset_snapshot=bundle.snapshot,
                active_manifest_keys=active_keys,
            )
        else:
            # Default synthetic research run with 1 injected edge (R-RES-10 acceptance criteria)
            register_synthetic_primitives()
            candles = edge_series(seed=args.seed, length=2000, win_probability_pct=65)
            snapshot = synthetic_snapshot(
                candles,
                asset="EURUSD-OTC",
                timeframe_s=60,
                seed=args.seed,
            )
            payout_lookup = PayoutLookup(
                [
                    PayoutPoint(
                        "EURUSD-OTC",
                        BASE_TS - BASE_TS % 3600 + offset * 3600,
                        Decimal("0.87"),
                        1,
                    )
                    for offset in range(50)
                ]
            )
            edge_cand = make_injected_edge_candidate("EURUSD-OTC")
            competing_res = enumerate_candidates(
                assets=["EURUSD-OTC"],
                max_candidates=min(args.max_candidates, 20),
                seed=args.seed,
            )
            candidates_pool = [edge_cand] + [
                c for c in competing_res.candidates if c.asset == "EURUSD-OTC" and c.tf == "M1"
            ][:10]

            out_dir = args.output_dir / "synthetic" / run_id
            res = run_research_pipeline(
                candles,
                payout_lookup,
                run_id=run_id,
                assets=["EURUSD-OTC"],
                seed=args.seed,
                max_candidates=args.max_candidates,
                output_dir=out_dir,
                dataset_snapshot=snapshot,
                override_candidates=candidates_pool,
                active_manifest_keys=active_keys,
                enforce_holdout_pass=False,
                min_oos_trades=50,
            )

        print(
            to_json(
                {
                    "event": "strategy_lab_research_completed",
                    "status": res.status,
                    "run_id": res.run_id,
                    "candidates_evaluated": res.candidates_count,
                    "approved_count": res.approved_count,
                    "dataset_fingerprint": res.dataset_fingerprint,
                    "dataset_origin": res.dataset_origin,
                    "production_eligible": res.production_eligible,
                    "grammar_audit": res.grammar_audit,
                    "ranking_md": str(res.ranking_md_path),
                    "candidates_json": str(res.candidates_json_path),
                }
            )
        )
        return 0 if res.status == "ok" else 1
    try:
        from strategy_lab.collect.iq_client import validate_asset

        asset = validate_asset(args.asset)
        output = args.output or (
            LAB_ROOT / "tests/fixtures/iq" / f"recorded-{asset}-{args.from_ts}-{args.to_ts}.json"
        )
        result = record_fixture(
            asset=asset,
            from_ts=args.from_ts,
            to_ts=args.to_ts,
            output=output,
        )
    except Exception:
        # No traceback or third-party exception content in the manual CLI.
        print(json.dumps({"event": "iq_fixture_record_failed", "status": "failed"}))
        return 1
    print(json.dumps({"event": "iq_fixture_recorded", **result}))
    return 0


def _dataset_from_supabase(assets: list[str], from_ts: int, to_ts: int) -> ResearchDataset:
    from strategy_lab.archive import EnvironmentColdArchiveReader
    from strategy_lab.archive_pg import PostgresArchiveRepository

    db_url = os.environ["SUPABASE_DB_URL"]
    archive_repository = PostgresArchiveRepository(db_url)
    return ResearchDataset.from_supabase(
        db_url,
        assets,
        from_ts,
        to_ts,
        cold_reader=EnvironmentColdArchiveReader(archive_repository),
    )


def _retention_repository(project_ref: str, environment: str) -> InventoryRepository:
    base_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if base_url and service_key:
        from strategy_lab.retention_rest import SupabaseRetentionInventory

        return SupabaseRetentionInventory(base_url, service_key, project_ref, environment)
    return FakeInventoryRepository(project_ref=project_ref, environment=environment)


def _archive_repository() -> OperationalArchiveRepository:
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if db_url:
        from strategy_lab.archive_pg import PostgresArchiveRepository

        return PostgresArchiveRepository(db_url)
    from strategy_lab.archive_rest import SupabaseRestArchiveRepository

    return SupabaseRestArchiveRepository(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
