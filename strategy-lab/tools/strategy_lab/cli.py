"""Strategy Lab CLI entry point; no connection on import/help."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from strategy_lab.collect.backup import BackupError, run_backup
from strategy_lab.collect.clock import Clock
from strategy_lab.collect.iq_client import LAB_ROOT, FakeIQClient, IQClient, IQClientProtocol
from strategy_lab.collect.pg_repository import PostgresRepository
from strategy_lab.collect.recorder import record_fixture
from strategy_lab.collect.repository import FakeRepository
from strategy_lab.collect.runner import fake_fixture_path, run_collect, status_report, to_json
from strategy_lab.research.dataset import ResearchDataset, coverage_report


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
    collect = subcommands.add_parser("collect", help="Executa coleta diaria do Strategy Lab.")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--payout-only", action="store_true")
    collect.add_argument("--assets", nargs="+", default=["EURUSD-OTC"])
    collect.add_argument("--from", dest="from_ts", type=parse_epoch)
    collect.add_argument("--force-source", action="store_true")
    status = subcommands.add_parser("status", help="Mostra saude da coleta.")
    status.add_argument("--dry-run", action="store_true")
    backup = subcommands.add_parser("backup", help="Backup criptografado do banco Strategy Lab.")
    backup.add_argument("--output-dir", type=Path)
    research = subcommands.add_parser("research", help="Ferramentas de pesquisa offline.")
    research.add_argument("--coverage-report", action="store_true")
    research.add_argument("--synthetic", action="store_true", help="Gera candidatos sintéticos.")
    research.add_argument("--seed", type=int, default=1, help="Seed determinística para pesquisa.")
    research.add_argument(
        "--max-candidates", type=int, default=5000, help="Limite máximo de candidatos."
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
            )
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
        except BackupError:
            print(to_json({"event": "strategy_lab_backup_failed", "status": "failed"}))
            return 1
        print(
            to_json({"event": "strategy_lab_backup_completed", "status": "ok", "path": str(target)})
        )
        return 0
    if args.command == "research":
        if args.coverage_report:
            try:
                dataset = (
                    ResearchDataset.from_parquet(
                        args.candles_parquet,
                        args.payouts_parquet,
                        args.gaps_parquet,
                    )
                    if args.candles_parquet and args.payouts_parquet
                    else ResearchDataset.from_supabase(
                        os.environ["SUPABASE_DB_URL"],
                        args.assets,
                        args.from_ts,
                        args.to_ts,
                    )
                )
                coverage_entries = coverage_report(dataset, args.assets, args.from_ts, args.to_ts)
            except Exception:
                print(to_json({"event": "strategy_lab_research_failed", "status": "failed"}))
                return 1
            print(to_json({"event": "strategy_lab_coverage_report", "assets": coverage_entries}))
            return 0 if all(bool(item["accepted"]) for item in coverage_entries) else 1

        import time
        from decimal import Decimal

        from strategy_lab.research.grammar import enumerate_candidates
        from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
        from strategy_lab.research.runner import run_research_pipeline
        from strategy_lab.research.synthetic import (
            BASE_TS,
            edge_series,
            make_injected_edge_candidate,
            register_synthetic_primitives,
        )

        run_id = args.run_id or f"run_{args.seed}_{int(time.time())}"
        out_dir = args.output_dir / run_id

        active_keys: set[str] = set()
        if args.active_manifest and args.active_manifest.exists():
            manifest_data = json.loads(args.active_manifest.read_text(encoding="utf-8"))
            active_keys = {s["key"] for s in manifest_data.get("strategies", []) if "key" in s}

        if args.candles_parquet and args.payouts_parquet:
            dataset = ResearchDataset.from_parquet(
                args.candles_parquet,
                args.payouts_parquet,
                args.gaps_parquet,
            )
            candles = dataset.candles_for(args.assets[0], args.from_ts, args.to_ts)
            payout_lookup = PayoutLookup.from_rows(dataset.payouts.to_dicts())
            res = run_research_pipeline(
                candles,
                payout_lookup,
                run_id=run_id,
                assets=args.assets,
                seed=args.seed,
                max_candidates=args.max_candidates,
                output_dir=out_dir,
                dataset=dataset,
                active_manifest_keys=active_keys,
            )
        else:
            # Default synthetic research run with 1 injected edge (R-RES-10 acceptance criteria)
            register_synthetic_primitives()
            candles = edge_series(seed=args.seed, length=2000, win_probability_pct=65)
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
                c for c in competing_res.candidates if c.asset == "EURUSD-OTC"
            ][:10]

            res = run_research_pipeline(
                candles,
                payout_lookup,
                run_id=run_id,
                assets=["EURUSD-OTC"],
                seed=args.seed,
                max_candidates=args.max_candidates,
                output_dir=out_dir,
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


if __name__ == "__main__":
    raise SystemExit(main())
