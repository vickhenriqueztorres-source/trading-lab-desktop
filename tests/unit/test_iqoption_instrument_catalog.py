from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.domain.market import (
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
)
from packages.domain.models import Broker
from packages.protocol.codec import encode_envelope
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.version import MAX_FRAME_SIZE


def instrument(index: int, product: BrokerInstrumentProduct) -> BrokerInstrument:
    symbol = f"S{index:03d}" + ("-OTC" if index % 2 else "")
    executable = product is BrokerInstrumentProduct.TURBO
    return BrokerInstrument(
        broker=Broker.IQ_OPTION,
        broker_id=f"{product.value}-{index}",
        broker_symbol=symbol,
        display_name=symbol,
        product=product,
        market_kind=BrokerMarketKind.OTC if symbol.endswith("-OTC") else BrokerMarketKind.REGULAR,
        availability=BrokerInstrumentAvailability.OPEN,
        duration_seconds=(60,) if executable else (),
        detectable=True,
        analyzable=executable,
        quotable=executable,
        executable=executable,
    )


def test_catalog_contract_keeps_product_and_otc_identity_exact() -> None:
    catalog = BrokerInstrumentCatalog(
        generation=7,
        observed_at_utc=datetime.now(UTC),
        instruments=(
            instrument(1, BrokerInstrumentProduct.TURBO),
            instrument(1, BrokerInstrumentProduct.DIGITAL),
            instrument(2, BrokerInstrumentProduct.BINARY),
        ),
    )
    restored = BrokerInstrumentCatalog.from_payload(catalog.to_payload())
    assert restored == catalog
    assert restored.instruments[0].broker_symbol == "S001-OTC"
    assert restored.instruments[0].market_kind is BrokerMarketKind.OTC
    assert restored.instruments[1].product is BrokerInstrumentProduct.DIGITAL
    assert not restored.instruments[1].executable


def test_complete_sanitised_catalogue_fits_bounded_ipc_frame() -> None:
    products = tuple(BrokerInstrumentProduct)
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=tuple(instrument(index, products[index % 3]) for index in range(510)),
    )
    envelope = Envelope(
        protocol_version=1,
        message_id="catalog-response",
        correlation_id="catalog-correlation",
        causation_id="catalog-request",
        source=EndpointRole.IQOPTION_WORKER,
        target=EndpointRole.CORE,
        message_type=MessageType.BROKER_INSTRUMENT_CATALOG_RESPONSE,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=catalog.to_payload(),
    )
    assert len(encode_envelope(envelope)) < MAX_FRAME_SIZE


def test_worker_catalogue_dispatch_is_read_only() -> None:
    calls: list[str] = []
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=(instrument(1, BrokerInstrumentProduct.DIGITAL),),
    )
    session = SimpleNamespace(
        is_connected=True,
        get_instrument_catalog=lambda: calls.append("catalog") or catalog,
    )
    server = IQOptionReadOnlyWorkerServer(
        "127.0.0.1",
        1,
        1,
        session,
        connection_mode="REAL_AUTH_READ_ONLY",
    )
    request = Envelope(
        protocol_version=1,
        message_id="request",
        correlation_id="correlation",
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.IQOPTION_WORKER,
        message_type=MessageType.BROKER_INSTRUMENT_CATALOG_REQUEST,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={},
    )
    response_type, payload = server._dispatch(request)
    assert response_type is MessageType.BROKER_INSTRUMENT_CATALOG_RESPONSE
    assert BrokerInstrumentCatalog.from_payload(payload) == catalog
    assert calls == ["catalog"]
    assert server._order_session is None
