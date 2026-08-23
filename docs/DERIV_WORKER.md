# Deriv Worker Read-Only

## Scope

The Deriv worker implemented in Phase 0 is a broker-isolated, read-only adapter for public market
data and explicit demo-session authentication architecture. It never submits, modifies or cancels an
order. The only executable default is the deterministic fake transport; network access to Deriv is
opt-in.

The Trading Core remains the only local financial authority. The worker owns only Deriv protocol
translation, raw response validation, websocket/REST transport details, subscriptions and
connection health. It does not receive the Core `state.db` path, Risk Ledger state, strategy state,
global balance or broker secrets.

## Official Deriv surfaces used

Only the following Deriv Options v1 read-only surfaces are modeled:

- Public websocket: `wss://api.derivws.com/trading/v1/options/ws/public`
- Demo websocket: `wss://api.derivws.com/trading/v1/options/ws/demo?otp=...`
- REST account discovery: `GET https://api.derivws.com/trading/v1/options/accounts`
- REST demo OTP: `POST https://api.derivws.com/trading/v1/options/accounts/{accountId}/otp`
- Public data operations: `ping`, `time`, `active_symbols`, `contracts_list`, `contracts_for`,
  `ticks`, `ticks_history`, `forget` and `forget_all`
- Demo read-only operation: `balance`

The implementation follows the Deriv developer documentation for API overview, websockets, active
symbols, ticks, ticks history, contracts metadata and options account/OTP flows.

## Read-only security policy

Trading operations are absent from the worker command surface and denied before the network
boundary. The denylist includes request keys and operations such as `buy`, `sell`, `proposal`,
`cancel`, `contract_update`, `auto_start`, `bulk_purchase`, `cashier`, `deposit` and `withdraw`.

The worker validates every outbound Deriv request against an allowlist. A forbidden opcode returns
`DERIV_TRADING_OPERATION_DISABLED` or `DERIV_OPERATION_NOT_ALLOWLISTED`; it is never sent to the
transport.

Real-mode endpoints are blocked before connection. `validate_deriv_ws_url` accepts only the exact
Deriv host, TLS websocket scheme, no userinfo, no port, no fragment, and one of these exact paths:

- `/trading/v1/options/ws/public`
- `/trading/v1/options/ws/demo` with exactly one `otp` query parameter

`/trading/v1/options/ws/real` raises `DERIV_REAL_WS_FORBIDDEN`. A selected REST account whose
`account_type` is not `demo` raises `DERIV_REAL_ACCOUNT_FORBIDDEN` before OTP is requested.

## Market data behavior

The worker normalizes Deriv payloads into immutable domain models:

- `MarketSymbol`
- `ContractMetadata`
- `MarketTick`
- `MarketCandle`
- `BrokerClockSnapshot`
- `BrokerAccountBalance` (demo session only)
- `BrokerCapabilities`

Prices, pips and offsets use `Decimal`; JSON `NaN`, `Infinity` and `-Infinity` fail closed as
schema incompatibility. External payloads are validated before becoming domain data.

Market health is explicit:

- `HEALTHY`: current accepted stream/history data
- `WARMING_UP`: connected but still restoring subscriptions or catalog
- `STALE`: monotonic stale threshold exceeded
- `GAPPED`: missing, late, out-of-order or overloaded ticks
- `DISCONNECTED`: transport not connected
- `INCOMPATIBLE`: schema drift or invalid critical payload

The real websocket transport has a dedicated reader thread. Responses with `req_id` are routed to
bounded pending request queues; unsolicited tick subscription messages are routed to a bounded
stream queue. The worker session drains that stream into `SubscriptionManager`, so the first
subscription tick and subsequent ticks pass through the same gap, duplicate, late and overload
checks before IPC publication.

Reconnect restores logical subscriptions and performs read-only tick-history backfill. Reconnect and
read-only retry use bounded attempts with backoff and jitter injection for deterministic tests.

A monotonic suspension detector invalidates market data when the local process observes a large
elapsed-time gap compatible with Windows sleep/resume. The session marks subscriptions as
restoring, sets health to `STALE` and requires reconnect/resynchronization before healthy data is
advertised again.

## Demo authentication architecture

Demo auth is explicit and separate from DualTrade identity/licensing:

- The worker receives a Deriv access token from a local token provider abstraction, not from the
  identity service.
- The REST client uses the token only inside the Deriv transport layer.
- Account discovery must find the exact user-selected account ID.
- The selected account must prove `account_type == "demo"` before OTP is requested.
- The OTP URL must validate as the exact demo websocket path before connection.
- `SecretValue` redacts token contents in `str()` and `repr()`.

The CLI wires this flow only when both controls are present: `--deriv-transport live-demo` and
`DUALTRADE_RUN_EXTERNAL_DERIV_DEMO=1`. It also requires `DUALTRADE_DERIV_APP_ID`,
`DUALTRADE_DERIV_DEMO_ACCOUNT_ID` and `DUALTRADE_DERIV_DEMO_TOKEN`. The token is read inside the
Deriv worker, never sent through DualTrade identity/licensing and never included in IPC, argv,
projection or exception text. The REST response's ready-to-use OTP URL is validated before opening
the websocket; the worker never constructs or accepts a real endpoint.

The Auth Agent, UI and Simulated Worker are spawned with broker credential environment variables
removed. This prevents the demo token from crossing into identity, presentation or the financial
simulator even when the Launcher/Core process tree was started from an opted-in shell.

`fake-public` remains the default. The complete selection is:

```text
fake-public  local deterministic public market-data fake (default)
fake-demo    local demo/read-only fake with synthetic balance and synchronized fake clock
live-public  external public read-only websocket, explicit CLI selection
live-demo    external demo read-only websocket, CLI selection plus environment opt-in
```

`fake-demo` is always labeled `FAKE SIMULADO` in the UI. Its balance is test data, never external
evidence. `live-demo` is labeled `DEMO LIVE`. Public mode does not request or fabricate account
balance because the public websocket is unauthenticated.

## IPC v1 integration

The Deriv worker process uses IPC v1 over loopback framed JSON, with endpoint role `DERIV_WORKER`.
Handshake capabilities announce:

- broker `DERIV`
- `connection_mode = PUBLIC_READ_ONLY` or `DEMO_AUTH_READ_ONLY`
- `supports_market_data = true`
- `can_submit_orders = false`
- no order status query and no order events

`BROKER_CLOCK_REQUEST/RESPONSE` returns the authoritative server epoch, observed UTC timestamp,
round-trip milliseconds, estimated offset and derived `is_synced`. The Core polls this evidence in
a bounded monitor. Round trip above 1,000 ms, absolute offset above 2,000 ms, timeout, crash or
invalid response adds `MD_CLOCK_UNTRUSTED` to the Core Health Gate; only a later proven-good sample
clears that specific blocker.

The demo OTP URL is single-use. A demo read timeout/disconnect therefore never reconnects or retries
that URL in place. It fails with closed health, and the explicit worker restart performs account
validation plus a fresh OTP bootstrap before producing new evidence.

`BROKER_BALANCE_REQUEST/RESPONSE` exists only for authenticated demo mode. The worker subscribes to
`balance`, validates stream events, converts the external numeric value through `Decimal` and emits
exact integer minor units plus ISO currency and `account_type=DEMO`. Unsupported fractional minor
units fail closed; public mode returns `DERIV_DEMO_AUTH_REQUIRED`. The account-event queue is
bounded and overflow returns `DERIV_ACCOUNT_EVENT_BACKPRESSURE`.

The Core-side generic read-only supervisor refuses any worker whose capabilities advertise
`can_submit_orders = true`. The Core contains no Deriv SDK/import dependency.

Market tick events are sent as unsolicited `MARKET_TICK_EVENT` envelopes. The original subscription
correlation ID is preserved in `correlation_id`; `causation_id` is `null` because stream ticks are
not direct responses.

In the continuous shadow composition, the Core consumes these immutable ticks only after the
series backfill has made Market Health healthy. The Core builds fixed-timeframe closed candles with
bounded deduplication and sends both history and live closures through the same durable ingress.
The worker still does not aggregate strategy state, decide stake, write SQLite or gain an order
surface. Reconnect invalidates the live bucket and subscription restore waits for the current Core
recovery generation.

Closed-candle ingress uses bounded `MARKET_HISTORY_REQUEST` calls with `style=candles`. The Core
client retains the response `message_id`, request `correlation_id` and `causation_id` in an
immutable `MarketHistoryBatch`. `DerivCandleHistoryPump` has no queue or hidden retry: it validates
the configured batch limit, converts only through `DerivCandleAdapter`, and terminates at the
persistent `CandleIngress`. Local integration tests run the real subprocess/IPC boundary with the
fake transport, including worker restart and idempotent redelivery.

The request optionally carries a positive `end_epoch`. This is a read-only pagination boundary used
by the Core `BackfillPlanner`; the worker forwards it as Deriv history `end` and never derives a
financial action from it. Retry, overlap, health and strategy delivery remain Core responsibilities.

## Phase 2 — Controlled Demo Execution and Reconciliation

In Phase 2 (Slice 2.1), the Deriv worker introduces controlled order submission and authoritative reconciliation strictly for **DEMO** accounts:

1. **Submission Guardrails (`can_submit_orders=true` on DEMO only)**:
   - Order submission is enabled exclusively when authenticated in a demo session (`connection_mode="DEMO"` or demo authenticated `order_session`).
   - Every attempt to submit an order with a real account ID (`CR...`), real websocket endpoint, or unauthenticated session fails closed raising `DERIV_REAL_ACCOUNT_FORBIDDEN`.
   - IPC `ORDER_SUBMIT` is translated to the Deriv `buy` request using contract parameters, stake minor units converted via exact `Decimal`, and immutable correlation tags embedded in `passthrough.order_id`.

2. **WebSocket Contract Event Streaming (`ORDER_EVENT`)**:
   - The worker streams contract lifecycle updates (`proposal_open_contract`) from the Deriv WebSocket into normalized `BrokerOrderEvent` envelopes (`ACCEPTED` → `OPEN` → `SETTLED`).
   - Settle events contain exact realized P&L integer minor units calculated from `payout` minus `buy_price` via `Decimal`, and full evidence hashes for tamper detection.
   - Events are pumped across loopback IPC to Core without blocking worker market data threads.

3. **Authoritative Reconciliation Handler (`ORDER_STATUS_REQUEST`)**:
   - The worker implements authoritative status query via Deriv `proposal_open_contract` (when `broker_order_id` is known) or `statement` transaction log matching `passthrough.order_id` (when `broker_order_id` is unknown after timeout).
   - Returns immutable `OrderStatusResult` with strict domain `ReconciliationEvidence` (`FOUND`, `NOT_FOUND`, or `UNAVAILABLE`).
   - Compares symbol, direction, and currency against original `OrderStatusQuery` to prevent mismatched reconciliation.

4. **Invariants Preserved**:
   - Single database writer transaction on `state.db` before dispatch.
   - Timeouts transition order to `UNKNOWN` while preserving active risk reservation.
   - Zero automatic blind retries on transient errors.
   - Settlement atomically commits realized P&L to `state.db` and releases risk exposure.

