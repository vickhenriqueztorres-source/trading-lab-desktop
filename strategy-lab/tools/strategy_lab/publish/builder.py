"""Manifest builder from research run candidates (R-PUB-1, R-PUB-5)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from manifest_schema.models import Management, Manifest, StrategyEntry, Validated
from sprt.test import WaldSprt

DEFAULT_PARITY_SHA256 = "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"
DEFAULT_PRIMITIVES_VERSION = "1.0.0"
DEFAULT_LIFETIME_SECONDS = 30 * 86400  # 30 days


class PromotionError(ValueError):
    """Raised when a candidate cannot be promoted from observation to approved."""


def build_manifest(
    run_id: str,
    candidates_data: dict[str, Any],
    current_manifest: Manifest | dict[str, Any] | None = None,
    include_keys: Sequence[str] | None = None,
    exclude_keys: Sequence[str] | None = None,
    promote_keys: Sequence[str] | None = None,
    live_outcomes_by_key: Mapping[str, Sequence[bool]] | None = None,
    live_days_by_key: Mapping[str, int] | None = None,
    key_id: str = "A",
    primitives_version: str = DEFAULT_PRIMITIVES_VERSION,
    primitives_parity_sha256: str = DEFAULT_PARITY_SHA256,
    now_epoch: int | None = None,
    manifest_version: int | None = None,
) -> Manifest:
    """Build unsigned Manifest model from candidates.json data with curation and promotion."""
    if now_epoch is None:
        now_epoch = int(datetime.now(UTC).timestamp())

    # Determine next manifest version
    curr_version = 0
    curr_strategies_by_key: dict[str, Any] = {}
    if current_manifest is not None:
        if isinstance(current_manifest, Manifest):
            curr_version = current_manifest.manifest_version
            for st in current_manifest.strategies:
                curr_strategies_by_key[st.key] = st
        elif isinstance(current_manifest, dict):
            curr_version = int(current_manifest.get("manifest_version", 0))
            for st_dict in current_manifest.get("strategies", []):
                curr_strategies_by_key[st_dict["key"]] = st_dict

    if manifest_version is None:
        manifest_version = curr_version + 1

    include_set = set(include_keys) if include_keys is not None else None
    exclude_set = set(exclude_keys) if exclude_keys is not None else set()
    promote_set = set(promote_keys) if promote_keys is not None else set()
    live_outcomes = live_outcomes_by_key or {}
    live_days = live_days_by_key or {}

    raw_candidates = candidates_data.get("candidates", [])
    strategies: list[StrategyEntry] = []

    for raw in raw_candidates:
        key = raw["key"]

        # Curation filters
        if include_set is not None and key not in include_set:
            continue
        if include_set is None and not raw.get("approved", False):
            continue
        if key in exclude_set:
            continue

        # Status determination (R-PUB-5)
        # Default for new entry is always "observation"
        target_status: Literal["approved", "observation", "rejected"] = "observation"

        # Check if already approved in current active manifest
        curr_entry = curr_strategies_by_key.get(key)
        curr_status = getattr(curr_entry, "status", None) or (
            curr_entry.get("status") if isinstance(curr_entry, dict) else None
        )
        if curr_status == "approved" and raw.get("approved", False):
            target_status = "approved"

        # Check explicit promotion request
        if key in promote_set:
            outcomes = live_outcomes.get(key, [])
            days = live_days.get(key, 0)
            p_0 = raw["validated"]["wilson_lower"]
            p_1 = raw["validated"]["p_min_at_validation"]

            sprt = WaldSprt(p_0=p_0, p_1=p_1)
            if not sprt.is_eligible_for_promotion(outcomes, days=days, min_ops=200, min_days=30):
                raise PromotionError(
                    f"Estratégia {key} não é elegível para promoção: "
                    f"ops={len(outcomes)} (exige >= 200) ou dias={days} (exige >= 30) "
                    f"sem rejeição SPRT."
                )
            target_status = "approved"

        val_dict = raw["validated"]
        validated = Validated(
            p_hat=val_dict["p_hat"],
            wilson_lower=val_dict["wilson_lower"],
            p_min_at_validation=val_dict["p_min_at_validation"],
            payout_min=val_dict["payout_min"],
            n=int(val_dict["n"]),
            ops_per_day=val_dict["ops_per_day"],
            worst_streak=int(val_dict["worst_streak"]),
            result_1000_ops_stake10=val_dict["result_1000_ops_stake10"],
            windows_passed=val_dict["windows_passed"],
            holdout_passed=bool(val_dict["holdout_passed"]),
        )

        mgmt_dict = raw.get("management", {})
        management = Management(
            stake_pct=mgmt_dict.get("stake_pct", "1.0"),
            martingale_steps_max=int(mgmt_dict.get("martingale_steps_max", 2)),
            paroli=bool(mgmt_dict.get("paroli", True)),
        )

        reason_pt = raw.get("reason_pt")
        if target_status == "rejected" and not reason_pt:
            reason_pt = "Reprovado nos critérios de pesquisa"

        entry = StrategyEntry(
            key=key,
            family=raw["family"],
            display_name_pt=raw["display_name_pt"],
            asset=raw["asset"],
            timeframe=raw["timeframe"],
            hours_utc=raw["hours_utc"],
            params=raw["params"],
            validated=validated,
            status=target_status,
            management=management,
            reason_pt=reason_pt,
        )
        strategies.append(entry)

    expires_at = now_epoch + DEFAULT_LIFETIME_SECONDS

    # Create unsigned Manifest model
    manifest = Manifest(
        schema_version=1,
        manifest_version=manifest_version,
        key_id=key_id,  # type: ignore[arg-type]
        published_at=now_epoch,
        expires_at=expires_at,
        primitives_version=primitives_version,
        primitives_parity_sha256=primitives_parity_sha256,
        research_run_id=run_id,
        strategies=strategies,
        signature="",
    )
    return manifest


def load_candidates_file(path: Path) -> dict[str, Any]:
    """Load candidates.json file from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de candidatos não encontrado: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
