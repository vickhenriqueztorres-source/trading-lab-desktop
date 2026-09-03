"""R-MAN-1..7: independent JSON Schema adapter for the documented mandatory policy.

Test-only jsonschema dependency. No Pydantic validation, production adapters or registry imports.
Deno must independently implement these rules and pass the public vectors before deployment.
"""

import re
from decimal import Decimal, localcontext

from jsonschema import Draft202012Validator, ValidationError, validators

POLICY_ID = "urn:strategy-lab:manifest-policy:v1"


def decimal_range(validator, spec, instance, schema):
    if not isinstance(instance, str) or re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", instance) is None:
        return
    number = Decimal(instance)
    lower, upper, step = (Decimal(spec[name]) for name in ("min", "max", "step"))
    if not lower <= number <= upper:
        yield ValidationError("MANIFEST_PARAM_RANGE")
    elif spec["kind"] == "int" and number != number.to_integral_value():
        yield ValidationError("MANIFEST_PARAM_INTEGER")
    else:
        n, d = number.as_integer_ratio()
        lo, ld = lower.as_integer_ratio()
        st, sd = step.as_integer_ratio()
        if ((n * ld - lo * d) * sd) % (d * ld * st):
            yield ValidationError("MANIFEST_PARAM_STEP")


def ordered_params(validator, pairs, instance, schema):
    if not isinstance(instance, dict):
        return
    for lower, upper in pairs:
        try:
            if Decimal(instance[lower]) >= Decimal(instance[upper]):
                yield ValidationError("MANIFEST_PARAM_RELATION")
        except (KeyError, ValueError, TypeError, ArithmeticError):
            pass  # Structural validator independently rejects missing/malformed fields.


def _finite_decimal(value):
    if not isinstance(value, str) or re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value) is None:
        raise ValueError
    return Decimal(value)


def policy(validator, policy_id, instance, schema):
    if policy_id != POLICY_ID:
        yield ValidationError("MANIFEST_POLICY_UNSUPPORTED")
        return
    if not isinstance(instance, dict):
        return
    try:
        published, expires = instance["published_at"], instance["expires_at"]
        if (
            type(published) is int
            and type(expires) is int
            and not 0 < expires - published <= 3888000
        ):
            yield ValidationError("MANIFEST_EXPIRATION")
        keys = []
        for entry in instance["strategies"]:
            keys.append(entry["key"])
            hours = entry["hours_utc"]
            if len(hours) == 2 and not hours[0] < hours[1]:
                yield ValidationError("MANIFEST_HOURS_RANGE")
            values = entry["validated"]
            p_hat = _finite_decimal(values["p_hat"])
            wilson = _finite_decimal(values["wilson_lower"])
            p_min = _finite_decimal(values["p_min_at_validation"])
            payout = _finite_decimal(values["payout_min"])
            if not all(Decimal(0) <= p <= Decimal(1) for p in (p_hat, wilson, p_min)):
                yield ValidationError("MANIFEST_PROBABILITY_RANGE")
            if wilson > p_hat:
                yield ValidationError("MANIFEST_WILSON_ABOVE_ESTIMATE")
            if _finite_decimal(values["ops_per_day"]) < 0:
                yield ValidationError("MANIFEST_OPS_NEGATIVE")
            if values["worst_streak"] > values["n"]:
                yield ValidationError("MANIFEST_STREAK_RANGE")
            passed, total = (int(x) for x in values["windows_passed"].split("/"))
            if passed > total:
                yield ValidationError("MANIFEST_WINDOWS_RANGE")
            with localcontext() as context:
                context.prec = 28
                if not 0 < payout <= 1 or payout % Decimal("0.01"):
                    yield ValidationError("MANIFEST_PAYOUT_MIN")
                elif wilson < 1 / (1 + payout) + Decimal("0.015"):
                    yield ValidationError("MANIFEST_PAYOUT_UNSAFE")
                elif payout > Decimal("0.01") and wilson >= (
                    1 / (1 + payout - Decimal("0.01")) + Decimal("0.015")
                ):
                    yield ValidationError("MANIFEST_PAYOUT_NOT_MINIMUM")
            if not 0 < _finite_decimal(entry["management"]["stake_pct"]) <= 100:
                yield ValidationError("MANIFEST_STAKE_RANGE")
        if len(keys) != len(set(keys)):
            yield ValidationError("MANIFEST_DUPLICATE_KEY")
    except (KeyError, ValueError, TypeError, ArithmeticError, IndexError):
        pass  # Let JSON Schema report malformed structure rather than throwing.


StrictValidator = validators.extend(
    Draft202012Validator,
    {
        "x-tl-policy-v1": policy,
        "x-tl-decimal-range": decimal_range,
        "x-tl-ordered-params": ordered_params,
    },
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)


def contract_validator(schema):
    if schema.get("x-tl-policy-v1") != POLICY_ID:
        raise ValueError("MANIFEST_POLICY_UNSUPPORTED")
    Draft202012Validator.check_schema(schema)
    return StrictValidator(schema)
