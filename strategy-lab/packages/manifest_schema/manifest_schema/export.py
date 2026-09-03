"""R-MAN-1/3: reproducible structural schema plus mandatory semantic vocabulary."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from manifest_schema.families import FAMILY_RELATIONS, FAMILY_SPECS
from manifest_schema.models import Manifest
from manifest_schema.rules import DECIMAL_PATTERN, MAX_DECIMAL_LENGTH

POLICY_ID = "urn:strategy-lab:manifest-policy:v1"


def _strict_pattern_ends(value: Any) -> None:
    # Python/ECMAScript $ may match before a final newline; Rust/Pydantic uses a strict end.
    # Portable negative lookahead makes the exported pattern a full-string boundary as well.
    if isinstance(value, dict):
        pattern = value.get("pattern")
        if isinstance(pattern, str) and pattern.endswith("$"):
            value["pattern"] = pattern + r"(?![\s\S])"
        for item in value.values():
            _strict_pattern_ends(item)
    elif isinstance(value, list):
        for item in value:
            _strict_pattern_ends(item)


def manifest_schema() -> dict[str, Any]:
    result = Manifest.model_json_schema()
    result["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    result["$id"] = "urn:strategy-lab:manifest:v1"
    result["$comment"] = (
        "REQUIRES manifest-policy-v1 in addition to Draft 2020-12 and Ed25519 verification. "
        "Reject if this semantic policy is unavailable; plain JSON Schema is NOT acceptance."
    )
    result["x-tl-policy-v1"] = POLICY_ID
    entry = result["$defs"]["StrategyEntry"]
    entry["allOf"] = []
    for family, specs in FAMILY_SPECS.items():
        properties = {
            name: {
                "type": "string",
                "pattern": DECIMAL_PATTERN,
                "minLength": 1,
                "maxLength": MAX_DECIMAL_LENGTH,
                "x-tl-decimal-range": {
                    "min": format(Decimal(spec.min), "f"),
                    "max": format(Decimal(spec.max), "f"),
                    "step": format(Decimal(spec.step), "f"),
                    "kind": spec.kind,
                },
            }
            for name, spec in specs.items()
        }
        entry["allOf"].append(
            {
                "if": {"properties": {"family": {"const": family}}, "required": ["family"]},
                "then": {
                    "properties": {
                        "params": {
                            "type": "object",
                            "properties": properties,
                            "required": list(specs),
                            "additionalProperties": False,
                            "x-tl-ordered-params": [
                                list(pair) for pair in FAMILY_RELATIONS[family]
                            ],
                        }
                    }
                },
            }
        )
    entry["allOf"].extend(
        [
            {
                "if": {"properties": {"status": {"const": "rejected"}}},
                "then": {
                    "required": ["reason_pt"],
                    "properties": {"reason_pt": {"type": "string", "pattern": r"\S"}},
                },
            },
            {
                "if": {"properties": {"status": {"const": "approved"}}},
                "then": {
                    "properties": {
                        "validated": {
                            "properties": {"holdout_passed": {"const": True}},
                        }
                    }
                },
            },
        ]
    )
    _strict_pattern_ends(result)
    return result


def schema_bytes() -> bytes:
    return (
        json.dumps(manifest_schema(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def export_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(schema_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Export R-MAN-1 manifest schema")
    parser.add_argument("--output", type=Path, default=Path("schema/manifest.v1.schema.json"))
    args = parser.parse_args()
    export_schema(args.output)


if __name__ == "__main__":
    main()
