"""R-MAN-1..7: strict wire models. Decimal strings are preserved, never normalized."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from manifest_schema.families import FAMILY_RELATIONS, FAMILY_SPECS, Family
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
        return self


class Manifest(WireModel):
    schema_version: Literal[1]
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
        keys = [entry.key for entry in self.strategies]
        if len(keys) != len(set(keys)):
            raise ValueError("MANIFEST_DUPLICATE_KEY")
        return self
