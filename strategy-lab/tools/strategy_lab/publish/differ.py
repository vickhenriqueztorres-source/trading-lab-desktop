"""Interactive manifest differ and strategy count confirmation (R-PUB-3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from manifest_schema.models import Manifest


@dataclass(frozen=True)
class ManifestDiff:
    old_version: int | None
    new_version: int
    added: list[str]
    removed: list[str]
    modified: list[str]
    unchanged: list[str]
    total_new: int


def compute_diff(
    old_manifest: Manifest | dict[str, Any] | None,
    new_manifest: Manifest | dict[str, Any],
) -> ManifestDiff:
    """Compute difference between previous active manifest and new candidate manifest."""
    old_strategies: dict[str, dict[str, Any]] = {}
    old_ver: int | None = None

    if old_manifest is not None:
        if isinstance(old_manifest, Manifest):
            old_ver = old_manifest.manifest_version
            for st in old_manifest.strategies:
                old_strategies[st.key] = st.model_dump()
        elif isinstance(old_manifest, dict):
            old_ver = old_manifest.get("manifest_version")
            for st_dict in old_manifest.get("strategies", []):
                old_strategies[st_dict["key"]] = st_dict

    new_strategies: dict[str, dict[str, Any]] = {}
    if isinstance(new_manifest, Manifest):
        new_ver = new_manifest.manifest_version
        for st in new_manifest.strategies:
            new_strategies[st.key] = st.model_dump()
    else:
        new_ver = int(new_manifest["manifest_version"])
        for st_dict in new_manifest.get("strategies", []):
            new_strategies[st_dict["key"]] = st_dict

    old_keys = set(old_strategies.keys())
    new_keys = set(new_strategies.keys())

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)

    modified: list[str] = []
    unchanged: list[str] = []

    for k in common:
        o = old_strategies[k]
        n = new_strategies[k]
        # Ignore volatile or formatting differences, compare key fields
        if (
            o.get("params") != n.get("params")
            or o.get("status") != n.get("status")
            or o.get("hours_utc") != n.get("hours_utc")
            or o.get("timeframe") != n.get("timeframe")
        ):
            modified.append(k)
        else:
            unchanged.append(k)

    return ManifestDiff(
        old_version=old_ver,
        new_version=new_ver,
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
        total_new=len(new_strategies),
    )


def format_diff_report(diff: ManifestDiff) -> str:
    """Format human-readable diff table."""
    prev_ver_str = f"v{diff.old_version}" if diff.old_version is not None else "N/A"
    lines: list[str] = [
        "============================================================",
        f"MANIFEST DIFF: {prev_ver_str} -> v{diff.new_version}",
        "============================================================",
        f"Adicionadas ({len(diff.added)}):",
    ]
    for k in diff.added:
        lines.append(f"  + {k}")
    if not diff.added:
        lines.append("  (nenhuma)")

    lines.append(f"\nRemovidas ({len(diff.removed)}):")
    for k in diff.removed:
        lines.append(f"  - {k}")
    if not diff.removed:
        lines.append("  (nenhuma)")

    lines.append(f"\nModificadas ({len(diff.modified)}):")
    for k in diff.modified:
        lines.append(f"  ~ {k}")
    if not diff.modified:
        lines.append("  (nenhuma)")

    lines.append(f"\nInalteradas: {len(diff.unchanged)}")
    lines.append(f"Total no novo manifesto: {diff.total_new}")
    lines.append("============================================================")
    return "\n".join(lines)


def prompt_confirmation(
    total_strategies: int,
    input_fn: Callable[[str], str] = input,
) -> bool:
    """Prompt user to confirm publish by typing exact count of strategies (R-PUB-3).

    Strictly forbids --yes bypass.
    """
    prompt_msg = (
        f"\nATENÇÃO: Confirmação obrigatória para publicação.\n"
        f"Digite exatamente o número total de estratégias ({total_strategies}) para confirmar: "
    )
    user_input = input_fn(prompt_msg).strip()
    return user_input == str(total_strategies)
