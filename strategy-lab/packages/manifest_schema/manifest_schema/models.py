"""R-MAN-1..7: strict wire models. Decimal strings are preserved, never normalized."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from manifest_schema.families import FAMILY_COMPONENTS, FAMILY_RELATIONS, FAMILY_SPECS, Family
from manifest_schema.recipe import RECIPE_IDENTITY_FIELDS, recipe_fingerprint
from manifest_schema.rules import (
    DECIMAL_PATTERN,
    MAX_DECIMAL_LENGTH,
    MAX_SAFE_INTEGER,
    decimal_value,
    validate_lifetime,
    validate_payout,
    validate_range,
)


def _checked_decimal(value: str) -> str:
    decimal_value(value)
    return value


type DecimalString = Annotated[
    str,
    Field(pattern=DECIMAL_PATTERN, min_length=1, max_length=MAX_DECIMAL_LENGTH),
    AfterValidator(_checked_decimal),
]
type Epoch = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
type Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
type Label = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[^\x00-\x1f]+$")]
type HashString = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", max_length=71)]
type Hour = Annotated[int, Field(ge=0, le=24)]
type KeyId = Literal["A", "B"]


class WireModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class Validated(WireModel):
    p_hat: DecimalString
    wilson_lower: DecimalString
    p_min_at_validation: DecimalString
    payout_min: DecimalString
    n: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    ops_per_day: DecimalString
    worst_streak: Count
    result_1000_ops_stake10: DecimalString
    windows_passed: Annotated[str, Field(pattern=r"^[0-9]{1,6}/[1-9][0-9]{0,5}$", max_length=13)]
    holdout_passed: bool

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        for value in (self.p_hat, self.wilson_lower, self.p_min_at_validation):
            if not 0 <= decimal_value(value) <= 1:
                raise ValueError("MANIFEST_PROBABILITY_RANGE")
        if decimal_value(self.wilson_lower) > decimal_value(self.p_hat):
            raise ValueError("MANIFEST_WILSON_ABOVE_ESTIMATE")
        if decimal_value(self.ops_per_day) < 0:
            raise ValueError("MANIFEST_OPS_NEGATIVE")
        if self.worst_streak > self.n:
            raise ValueError("MANIFEST_STREAK_RANGE")
        passed, total = (int(part) for part in self.windows_passed.split("/"))
        if passed > total:
            raise ValueError("MANIFEST_WINDOWS_RANGE")
        validate_payout(self.wilson_lower, self.payout_min)
        return self


class Management(WireModel):
    stake_pct: DecimalString
    martingale_steps_max: Annotated[int, Field(ge=0, le=10)]
    paroli: bool

    @field_validator("stake_pct")
    @classmethod
    def validate_stake(cls, value: str) -> str:
        if not 0 < decimal_value(value) <= 100:
            raise ValueError("MANIFEST_STAKE_RANGE")
        return value


class Composition(WireModel):
    regime: Literal["adx", "ema_alignment", "session_window", "bb_width_ratio"]
    trigger: Literal[
        "bb_close_outside", "ema_pullback", "level_touch", "range_break", "quadrant_majority"
    ]
    confirm: Literal["candle_rejection", "rsi_extreme", "tick_volume_ratio"]


class RequiredCapabilities(WireModel):
    product: Literal["binary_option"]
    tick_volume: bool


class DatasetEvidence(WireModel):
    dataset_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]+$", min_length=1, max_length=120)]
    fingerprint: HashString
    kind: Literal["real_market", "synthetic"]
    from_ts: Epoch
    to_ts: Epoch
    coverage_pct: DecimalString

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        if self.to_ts <= self.from_ts:
            raise ValueError("MANIFEST_DATASET_RANGE")
        if not 0 <= decimal_value(self.coverage_pct) <= 100:
            raise ValueError("MANIFEST_COVERAGE_RANGE")
        return self


class TelemetryPolicy(WireModel):
    outcomes_supported: bool
    opt_in_required: Literal[True]
    schema_version: Literal[1]


class StrategyEntry(WireModel):
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_:.-]+$", min_length=1, max_length=160)]
    family: Family
    display_name_pt: Label
    asset: Annotated[str, Field(pattern=r"^[A-Z0-9]+(?:-OTC)?$", min_length=1, max_length=32)]
    timeframe: Literal["M1", "M5", "M15"]
    hours_utc: Annotated[list[Hour], Field(min_length=2, max_length=2)]
    params: dict[str, DecimalString]
    validated: Validated
    status: Literal["approved", "observation", "rejected"]
    management: Management
    reason_pt: Label | None = None
    warmup_required: Annotated[int, Field(ge=1, le=10_000)] | None = None
    recipe_revision: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)] | None = None
    recipe_fingerprint: HashString | None = None
    composition: Composition | None = None
    capabilities: RequiredCapabilities | None = None

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        if not self.hours_utc[0] < self.hours_utc[1]:
            raise ValueError("MANIFEST_HOURS_RANGE")
        specs = FAMILY_SPECS[self.family]
        if self.params.keys() != specs.keys():
            raise ValueError("MANIFEST_PARAM_KEYS")
        for name, spec in specs.items():
            validate_range(self.params[name], spec)
        for lower, upper in FAMILY_RELATIONS[self.family]:
            if decimal_value(self.params[lower]) >= decimal_value(self.params[upper]):
                raise ValueError("MANIFEST_PARAM_RELATION")
        if self.status == "rejected" and (self.reason_pt is None or not self.reason_pt.strip()):
            raise ValueError("MANIFEST_REASON_REQUIRED")
        if self.status == "approved" and not self.validated.holdout_passed:
            raise ValueError("MANIFEST_HOLDOUT_REQUIRED")
        if self.composition is not None:
            declared = (
                self.composition.regime,
                self.composition.trigger,
                self.composition.confirm,
            )
            if declared != FAMILY_COMPONENTS[self.family]:
                raise ValueError("MANIFEST_COMPOSITION_MISMATCH")
        if self.capabilities is not None:
            needs_volume = "tick_volume_ratio" in FAMILY_COMPONENTS[self.family]
            if self.capabilities.tick_volume != needs_volume:
                raise ValueError("MANIFEST_VOLUME_CAPABILITY_MISMATCH")
        if self.recipe_fingerprint is not None:
            document = self.model_dump(mode="json", exclude_none=True)
            if any(name not in document for name in RECIPE_IDENTITY_FIELDS):
                raise ValueError("MANIFEST_RECIPE_CONTRACT_REQUIRED")
            expected = recipe_fingerprint(document)
            if self.recipe_fingerprint != expected:
                raise ValueError("MANIFEST_RECIPE_FINGERPRINT")
        return self


class Manifest(WireModel):
    schema_version: Literal[1]
    schema_revision: Literal["1.1", "1.2"] = "1.1"
    manifest_version: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    key_id: KeyId
    published_at: Epoch
    expires_at: Epoch
    primitives_version: Annotated[
        str, Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=32)
    ]
    primitives_parity_sha256: HashString
    research_run_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9_.-]+$", min_length=1, max_length=96)
    ]
    execution_semantics_version: Literal["tl.candle-close.v2"] | None = None
    dataset_evidence: DatasetEvidence | None = None
    telemetry: TelemetryPolicy | None = None
    strategies: Annotated[list[StrategyEntry], Field(max_length=5000)]
    # Empty only while building locally; verify always rejects unsigned manifests.
    signature: Annotated[
        str, Field(pattern=r"^(?:|ed25519:[A-Za-z0-9+/]{86}==)$", max_length=96)
    ] = ""

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        # Literal equality in Python otherwise admits True and 1.0.
        if type(value) is not int:
            raise ValueError("MANIFEST_SCHEMA_VERSION")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        validate_lifetime(self.published_at, self.expires_at)
        if (
            self.schema_revision in {"1.1", "1.2"}
            and "schema_revision" in self.model_fields_set
            and any(entry.warmup_required is None for entry in self.strategies)
        ):
            raise ValueError("MANIFEST_WARMUP_REQUIRED")
        if self.schema_revision == "1.2" and "schema_revision" in self.model_fields_set:
            if self.execution_semantics_version is None:
                raise ValueError("MANIFEST_EXECUTION_SEMANTICS_REQUIRED")
            if self.dataset_evidence is None:
                raise ValueError("MANIFEST_DATASET_EVIDENCE_REQUIRED")
            if self.telemetry is None:
                raise ValueError("MANIFEST_TELEMETRY_POLICY_REQUIRED")
            for entry in self.strategies:
                if (
                    entry.recipe_revision is None
                    or entry.recipe_fingerprint is None
                    or entry.composition is None
                    or entry.capabilities is None
                ):
                    raise ValueError("MANIFEST_RECIPE_CONTRACT_REQUIRED")
            if self.dataset_evidence.kind == "synthetic" and any(
                entry.status == "approved" for entry in self.strategies
            ):
                raise ValueError("MANIFEST_SYNTHETIC_APPROVAL")
        else:
            if any(
                value is not None
                for value in (
                    self.execution_semantics_version,
                    self.dataset_evidence,
                    self.telemetry,
                )
            ) or any(
                value is not None
                for entry in self.strategies
                for value in (
                    entry.recipe_revision,
                    entry.recipe_fingerprint,
                    entry.composition,
                    entry.capabilities,
                )
            ):
                raise ValueError("MANIFEST_RECIPE_CONTRACT_REVISION")
        keys = [entry.key for entry in self.strategies]
        if len(keys) != len(set(keys)):
            raise ValueError("MANIFEST_DUPLICATE_KEY")
        return self
