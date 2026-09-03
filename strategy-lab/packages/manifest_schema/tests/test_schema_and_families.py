"""R-MAN-1/3/5/6/7: exported schema, registry provenance, boundaries and cross-field rules."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from manifest_schema.export import export_schema, manifest_schema, schema_bytes
from manifest_schema.families import (
    FAMILY_BINDINGS,
    FAMILY_COMPONENTS,
    FAMILY_GATES,
    FAMILY_SPECS,
)
from manifest_schema.models import Manifest
from manifest_schema.rules import decimal_value, validate_range
from primitives.base import Category
from primitives.registry import REGISTRY
from pydantic import ValidationError
from schema_oracle import contract_validator

ROOT = Path(__file__).resolve().parents[3]


def test_export_is_synchronized(tmp_path):
    """R-MAN-1: checked-in public schema must match deterministic export byte for byte."""
    assert (ROOT / "schema/manifest.v1.schema.json").read_bytes() == schema_bytes()
    path = tmp_path / "export/schema.json"
    export_schema(path)
    assert path.read_bytes() == schema_bytes()
    contract_validator(manifest_schema())


def test_each_family_derives_registry_specs():
    """R-MAN-3: exactly one primitive per category and explicit separately owned gates."""
    for family, components in FAMILY_COMPONENTS.items():
        assert [REGISTRY[name].category for name in components] == [
            Category.REGIME,
            Category.TRIGGER,
            Category.CONFIRM,
        ]
        for wire, (name, parameter) in FAMILY_BINDINGS[family].items():
            assert FAMILY_SPECS[family][wire] == REGISTRY[name].param_spec[parameter]
        assert FAMILY_SPECS[family].keys() == (
            FAMILY_BINDINGS[family].keys() | FAMILY_GATES[family].keys()
        )


def test_all_parameter_ranges_enforced():
    """R-MAN-3: every declared parameter rejects both sides of its permitted domain."""
    for specs in FAMILY_SPECS.values():
        for spec in specs.values():
            validate_range(format(Decimal(spec.min), "f"), spec)
            validate_range(format(Decimal(spec.max), "f"), spec)
            for outside in (
                Decimal(spec.min) - Decimal(spec.step),
                Decimal(spec.max) + Decimal(spec.step),
            ):
                with pytest.raises(ValueError, match="MANIFEST_PARAM_RANGE"):
                    validate_range(format(outside, "f"), spec)


@pytest.mark.parametrize("value", ["1e2", "NaN", "Infinity", "+1", " 1", "1\n", "١", "1" * 25])
def test_decimal_lexemes_invalid(value):
    """R-MAN-1: regex is full-string, ASCII and bounded rather than a permissive parser."""
    with pytest.raises(ValueError, match="MANIFEST_DECIMAL_INVALID"):
        decimal_value(value)


def test_cross_family_relations_and_unknown_params():
    """R-MAN-3: independent schema and model agree on relations absent from scalar ranges."""
    vectors = json.loads((ROOT / "contracts/manifest_acceptance_vectors.json").read_text("utf-8"))
    oracle = contract_validator(manifest_schema())
    for family, bad in [
        ("F2", {"ema_short": "20", "ema_medium": "10"}),
        ("F3", {"level_support": "2", "level_resistance": "1"}),
        ("F5", {"quadrant_window": "4"}),
    ]:
        case = next(x for x in vectors["cases"] if x["id"] == "valid_" + family.lower())
        doc = deepcopy(case["document"])
        doc["strategies"][0]["params"].update(bad)
        with pytest.raises(ValidationError):
            Manifest.model_validate(doc)
        assert not oracle.is_valid(doc)


def test_fixture_matches_documented_architecture(fixture_document):
    """R-MAN-1/2/4: Architecture §6 is the exact signed offline fixture, not placeholders."""
    architecture = (ROOT / "01-ARCHITECTURE.md").read_text("utf-8")
    section = architecture.split("## 6. Manifesto — o contrato", 1)[1]
    example = section.split("```json", 1)[1].split("```", 1)[0]
    assert json.loads(example) == fixture_document


@pytest.mark.parametrize(
    "path",
    [
        ["primitives_version"],
        ["research_run_id"],
        ["primitives_parity_sha256"],
        ["strategies", 0, "params", "bb_k"],
        ["strategies", 0, "validated", "result_1000_ops_stake10"],
        ["strategies", 0, "validated", "windows_passed"],
    ],
)
def test_trailing_newline_is_rejected_by_both_validators(fixture_document, path):
    """R-MAN-1: Pydantic and Python/ECMAScript regex end-anchor semantics cannot diverge."""
    item = fixture_document
    for name in path[:-1]:
        item = item[name]
    item[path[-1]] += "\n"
    with pytest.raises(ValidationError):
        Manifest.model_validate(fixture_document)
    assert not contract_validator(manifest_schema()).is_valid(fixture_document)


def test_export_cli(tmp_path, monkeypatch):
    """R-MAN-1: the explicit exporter writes only to its requested output path."""
    from manifest_schema.export import main

    path = tmp_path / "schema.json"
    monkeypatch.setattr("sys.argv", ["export", "--output", str(path)])
    main()
    assert path.read_bytes() == schema_bytes()


def test_structural_mutations_match_schema_and_pydantic(fixture_document):
    """R-MAN-1/3: deterministic malformed types at every leaf, independently validated."""
    oracle = contract_validator(manifest_schema())

    def paths(value, prefix=()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from paths(item, (*prefix, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from paths(item, (*prefix, index))
        else:
            yield prefix

    for path in paths(fixture_document):
        for bad in (None, True, False, {}, [], "", "NaN", "1e2"):
            changed = deepcopy(fixture_document)
            target = changed
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = bad
            try:
                Manifest.model_validate(changed)
                model_valid = True
            except ValidationError:
                model_valid = False
            assert oracle.is_valid(changed) is model_valid, (path, bad)
