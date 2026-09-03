"""R-MAN-1..7 semantic rules; pure, bounded and free of Pydantic or I/O."""

import re
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from primitives.base import ParamRange

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
MAX_DECIMAL_LENGTH = 24
MAX_LIFETIME = 45 * 86400
MAX_SAFE_INTEGER = 9007199254740991
MARGIN = Decimal("0.015")
PAYOUT_STEP = Decimal("0.01")


def decimal_value(value: str) -> Decimal:
    """Reject exponent, whitespace, Unicode digits, excessive input and non-finite values."""
    if len(value) > MAX_DECIMAL_LENGTH or re.fullmatch(DECIMAL_PATTERN, value) is None:
        raise ValueError("MANIFEST_DECIMAL_INVALID")
    return Decimal(value)


def validate_range(value: str, spec: ParamRange) -> None:
    number = decimal_value(value)
    minimum, maximum, step = Decimal(spec.min), Decimal(spec.max), Decimal(spec.step)
    if not minimum <= number <= maximum:
        raise ValueError("MANIFEST_PARAM_RANGE")
    if spec.kind == "int" and number != number.to_integral_value():
        raise ValueError("MANIFEST_PARAM_INTEGER")
    # Exact integer arithmetic over Decimal ratios avoids a rounded modulo at the boundary.
    n, d = number.as_integer_ratio()
    lo, ld = minimum.as_integer_ratio()
    st, sd = step.as_integer_ratio()
    if ((n * ld - lo * d) * sd) % (d * ld * st):
        raise ValueError("MANIFEST_PARAM_STEP")


def validate_lifetime(published: int, expires: int) -> None:
    if not 0 < expires - published <= MAX_LIFETIME:
        raise ValueError("MANIFEST_EXPIRATION")


def validate_payout(wilson: str, payout: str) -> None:
    """R-MAN-7: smallest safe payout on the published 0.01 grid, rounded upward."""
    lower, minimum = decimal_value(wilson), decimal_value(payout)
    if not Decimal(0) < minimum <= Decimal(1):
        raise ValueError("MANIFEST_PAYOUT_MIN")
    if minimum % PAYOUT_STEP:
        raise ValueError("MANIFEST_PAYOUT_GRID")
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        if lower < Decimal(1) / (Decimal(1) + minimum) + MARGIN:
            raise ValueError("MANIFEST_PAYOUT_UNSAFE")
        previous = minimum - PAYOUT_STEP
        if previous > 0 and lower >= Decimal(1) / (Decimal(1) + previous) + MARGIN:
            raise ValueError("MANIFEST_PAYOUT_NOT_MINIMUM")
