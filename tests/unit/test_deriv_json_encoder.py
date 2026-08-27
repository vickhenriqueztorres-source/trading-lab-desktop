from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.deriv_worker.websocket_client import encode_deriv_json


def test_deriv_json_encoder_preserves_decimal_money_as_number() -> None:
    raw = encode_deriv_json({"amount": Decimal("0.35"), "price": Decimal("10.00")})
    parsed = json.loads(raw, parse_float=Decimal, parse_int=Decimal)

    assert parsed == {"amount": Decimal("0.35"), "price": Decimal("10.00")}
    assert '"0.35"' not in raw


def test_deriv_json_encoder_rejects_binary_float() -> None:
    with pytest.raises(TypeError):
        encode_deriv_json({"amount": 0.35})
