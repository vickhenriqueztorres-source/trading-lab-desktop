# Deriv Worker — Market Data and Selected-Account Execution

## Scope

The Deriv worker is a broker-isolated adapter for public market data and explicit authenticated
Options execution. `fake-public` remains the only default. A user-entered API Token/PAT is validated
inside the application; the official account list supplies Demo and Real choices. `live-demo` or
`live-real` is selected only after the user chooses one account. Real is never auto-selected.

The Trading Core remains the only local financial authority. The worker owns only Deriv protocol
translation, raw response validation, websocket/REST transport details, subscriptions and
connection health. It does not receive the Core `state.db` path, Risk Ledger state, strategy state,
global balance or broker secrets.

## Official Deriv surfaces used

The following Deriv Options v1 surfaces are modeled:

- Public websocket: `wss://api.derivws.com/trading/v1/options/ws/public`
- Demo websocket: `wss://api.derivws.com/trading/v1/options/ws/demo?otp=...`
- Real websocket: `wss://api.derivws.com/trading/v1/options/ws/real?otp=...`
- REST account discovery: `GET https://api.derivws.com/trading/v1/options/accounts`
- REST account OTP: `POST https://api.derivws.com/trading/v1/options/accounts/{accountId}/otp`
- Public data operations: `ping`, `time`, `active_symbols`, `contracts_list`, `contracts_for`,
  `ticks`, `ticks_history`, `forget` and `forget_all`
- Account operation: `balance`
- Execution and lifecycle operations: `proposal`, `buy`, `proposal_open_contract`, `statement`,
  `profit_table` and `forget`

The implementation follows the Deriv developer documentation for API overview, websockets, active
symbols, ticks, ticks history, contracts metadata and options account/OTP flows.

## Account-mode security policy

The worker validates every outbound request against a mode-specific allowlist. Public and Real mode
are strictly read-only. Authenticated Demo adds only the operations required to quote, buy and
reconcile options contracts. Sell/cancel mutation, cashier, deposit,
withdrawal and every unlisted opcode fail before the transport boundary.

`validate_deriv_ws_url` accepts only the exact Deriv host, TLS websocket scheme, no userinfo, no
port, no fragment, and the exact path for the explicitly expected account type:

- `/trading/v1/options/ws/public`
- `/trading/v1/options/ws/demo` with exactly one `otp` query parameter
- `/trading/v1/options/ws/real` with exactly one `otp` query parameter

A mode mismatch fails before the trading connection is exposed. Demo cannot accept a Real endpoint
or account, and Real cannot accept a Demo endpoint or account. Real never constructs or advertises
an order-submission session in this release.

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

Public reconnect restores logical subscriptions and performs read-only tick-history backfill. An
authenticated disconnect is different: the Core closes new entries, replaces the worker, requests
a fresh single-use OTP, reconciles non-terminal orders and restores subscriptions. Attempts use
capped backoff; no potentially accepted financial command is resent.

A monotonic suspension detector invalidates market data when the local process observes a large
elapsed-time gap compatible with Windows sleep/resume. The session marks subscriptions as
restoring, sets health to `STALE` and requires reconnect/resynchronization before healthy data is
advertised again.

## Token-only authentication architecture

Demo auth is explicit and separate from DualTrade identity/licensing:

- The worker receives a Deriv access token from a local token provider abstraction, not from the
  identity service.
- The REST client uses the token only inside the Deriv transport layer.
- Account discovery must find the exact user-selected account ID.
- The selected account must prove `account_type == "demo"` or `"real"` and match the requested mode.
- The OTP URL must validate as the exact matching websocket path before connection.
- `SecretValue` redacts token contents in `str()` and `repr()`.

### Desktop login flow

The packaged Windows application always opens its normal main window first in public read-only
mode. Inside `Deriv > Configuration`, `Conectar conta Deriv` opens an isolated credential helper.
The user supplies only an OAuth/PAT token with `trade` permission. The product App ID is internal.
The helper queries active Options accounts and displays Demo before Real without preselecting either.
Choosing Real reveals a warning, requires a checkbox and requires typing `REAL`. Cancelling leaves
the already-open application in public read-only mode.

The dialog writes the selected account ID/type and token directly to `broker_credentials/` through
DPAPI CurrentUser. The App ID is public product configuration and is not written to the vault.
Only encrypted `.vault` envelopes are persisted. The credential-entry helper and Deriv worker are
the only processes that handle the token; the main UI and Core pass only the vault directory path
or a non-secret connect command. Auth Agent, main UI and Simulated Worker continue receiving
sanitized environments without broker credentials.

The worker does not infer account mode from an account-ID prefix. It retrieves the selected account
from the official Options accounts endpoint, requires the saved account type to match and validates
the exact Demo or Real OTP WebSocket endpoint. A failed authenticated connection presents a visible
error while the application remains available in public read-only mode.

After credentials are saved, a versioned local IPC command asks the Core to replace the public
worker with `live-demo` or `live-real`. Later clicks can reuse the DPAPI-protected credentials
without retyping. Switching account type is blocked while a Deriv order is non-terminal.
The legacy environment-based bootstrap remains available only for controlled development. The
token is never included in broker IPC, argv, projection or exception text. The REST response's
ready-to-use OTP URL is validated before opening the websocket; the worker never constructs an OTP
URL or accepts an account-mode mismatch.

The Auth Agent, UI and Simulated Worker are spawned with broker credential environment variables
removed. This prevents the demo token from crossing into identity, presentation or the financial
simulator even when the Launcher/Core process tree was started from an opted-in shell.

`fake-public` remains the default. The complete selection is:

```text
fake-public  local deterministic public market-data fake (default)
fake-demo    local Demo read-only fake with synthetic balance and synchronized fake clock
live-public  external public read-only websocket, explicit CLI selection
live-demo    external Demo websocket with balance and controlled execution, selection plus opt-in
live-real    external Real websocket, explicit selection/confirmation and real authorization gate
```

`fake-demo` is always labeled `FAKE SIMULADO` in the UI. Its balance is test data, never external
evidence. `live-demo` is labeled `DEMO LIVE`; `live-real` is labeled
`REAL — DINHEIRO REAL` and changes the window badge/title. Public mode does not request or fabricate account
balance because the public websocket is unauthenticated.

## IPC v1 integration

The Deriv worker process uses IPC v1 over loopback framed JSON, with endpoint role `DERIV_WORKER`.
Handshake capabilities are mode-dependent. Public mode announces:

- broker `DERIV`
- `connection_mode = PUBLIC_READ_ONLY`, `DEMO_AUTH_READ_ONLY` or `REAL_AUTH_READ_ONLY`
- `supports_market_data = true`
- `can_submit_orders = false`
- no order status query and no order events

Only an authenticated Demo session additionally announces `can_submit_orders = true`,
`supports_order_status_query = true`, `supports_reconciliation = true` and
`supports_order_events = true`. The generic read-only supervisor still rejects those capabilities;
the financial Core must use the order-aware worker boundary.

`BROKER_CLOCK_REQUEST/RESPONSE` returns the authoritative server epoch, observed UTC timestamp,
round-trip milliseconds, estimated offset and derived `is_synced`. The Core polls this evidence in
a bounded monitor. Round trip above 1,000 ms, absolute offset above 2,000 ms, timeout, crash or
invalid response adds `MD_CLOCK_UNTRUSTED` to the Core Health Gate; only a later proven-good sample
clears that specific blocker.

The demo OTP URL is single-use. A demo read timeout/disconnect therefore never reconnects or retries
that URL in place. It fails with closed health, and supervised Core recovery replaces the worker,
performs account validation plus a fresh OTP bootstrap, reconciles and then produces new evidence.

`BROKER_BALANCE_REQUEST/RESPONSE` exists only for authenticated demo mode. The worker subscribes to
`balance`, validates stream events, converts the external numeric value through `Decimal` and emits
exact integer minor units plus ISO currency and `account_type=DEMO`. Unsupported fractional minor
units fail closed; public mode returns `DERIV_DEMO_AUTH_REQUIRED`. The account-event queue is
bounded and overflow returns `DERIV_ACCOUNT_EVENT_BACKPRESSURE`.

The Core-side generic read-only supervisor refuses any worker whose capabilities advertise
`can_submit_orders = true`. The Core contains no Deriv SDK/import dependency.

Market tick events are sent as unsolicited `MARKET_TICK_EVENT` envelopes. The original subscription
correlation ID is preserved in `correlation_id`; `causation_id` is `null` because stream ticks are
not direct responses. Each event also carries a bounded `digit_frequency` snapshot for the active
synthetic index. The worker maintains a fixed-capacity circular buffer, ten frequency counters and
a 10x10 first-order transition matrix. Insertions and evictions update these structures in O(1),
using `Decimal` quotes and a monotonic receipt clock. This telemetry is observational; it is not a
prediction, trading signal or profit claim.

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

Tick-history warm-up is also paginated at the IPC boundary. A worker response contains at most 100
ticks, while the Core assembles the 500-tick Digit Edge window through bounded backward pages,
deduplicates epochs and sorts the final window. This preserves the 64 KiB IPC frame limit instead of
allowing an oversized response to terminate the worker. The authenticated WebSocket receive stream
is protected by one session I/O lock, so request/response traffic and the live subscription pump
cannot consume each other's messages. A UI-process loss now triggers safe shutdown of the complete
supervised tree and releases the profile lock, preventing an invisible stale instance from blocking
the next launch.

The Core also maintains a bounded read-only Shadow universe for `R_10`, `R_25`, `R_50`, `R_75`
and `R_100`. Availability is discovered through `active_symbols` and verified through
`contracts_for`; each accepted symbol receives an independent subscription and paged 500-tick
history. The worker remains a transport adapter: it does not rank assets or gain any new authority.
One secondary subscription failure stays isolated from the operator-selected stream and therefore
cannot close its health gate. The Core's ranking is observational only and never routes an order.

## Phase 2 — Controlled Selected-Account Execution and Reconciliation

The Deriv worker provides controlled order submission and authoritative reconciliation for the
explicitly selected Options account. Demo is the default testing path; Real requires all additional
gates documented above.

1. **Submission Guardrails**:
   - Submission is enabled only after authenticated capabilities match the selected Demo/Real mode.
   - IPC `ORDER_SUBMIT` is rejected locally with `ORDER_COMMAND_EXPIRED` when its UTC deadline has
     elapsed. Otherwise the worker requests a `proposal` with `underlying_symbol`, exact `Decimal`
     stake and duration, then sends `buy` using only the returned proposal ID and maximum price.
     Immutable `passthrough.order_id` and `correlation_id` remain attached.
   - Network failure or timeout after the send boundary returns `TIMEOUT_AFTER_POSSIBLE_SEND`; the
     Core records `UNKNOWN`, preserves the reservation and performs zero automatic submission retry.
   - `DIGITDIFF` is the one explicit direct-buy exception. Its already-persisted `OrderCommand`
     includes the prediction digit and produces `buy: 1` with `basis=stake`, `duration=1`,
     `duration_unit=t`, a string barrier and immutable passthrough IDs. Other contract types retain
     the proposal-ID route.

2. **WebSocket Contract Event Streaming (`ORDER_EVENT`)**:
   - The worker subscribes immediately after `buy`, normalizes `OPEN` updates with remaining seconds
     and current spot when supplied, and emits `SETTLED` when Deriv proves expiry/sale or a terminal
     `won`/`lost` status.
   - Settlement uses the official `profit` value, falling back to `payout - buy_price`, converts
     through `Decimal` to integer minor units, hashes the canonical evidence and forgets the finished
     subscription.
   - For `DIGITDIFF`, the worker additionally validates the last digit of official `exit_tick`
     (accepting `exit_spot` from the current API schema) against the stored barrier. A disagreement
     between digit outcome and official status/profit fails closed instead of fabricating a result.
   - Events are pumped across loopback IPC to Core without blocking worker market data threads.

3. **Authoritative Reconciliation Handler (`ORDER_STATUS_REQUEST`)**:
   - The worker implements authoritative status query via `proposal_open_contract` when the contract
     ID is known, or bounded `statement` followed by `profit_table` matching the exact
     `passthrough.order_id` when it is available.
   - Current Deriv `statement`/`profit_table` responses may omit `passthrough`. For an ambiguous buy,
     the Core therefore transmits its durable submission timestamp. The worker may recover a missing
     contract ID only when `profit_table` yields exactly one record inside the bounded post-submit
     window with the same symbol, contract type and exact Decimal stake. Zero or multiple matches
     remain unresolved; there is no heuristic release of risk.
   - Returns immutable `OrderStatusResult` with strict domain `ReconciliationEvidence` (`FOUND`, `NOT_FOUND`, or `UNAVAILABLE`).
   - Compares symbol, direction, stake amount and currency against the original query before producing
     evidence. Missing external proof stays unresolved; it never releases risk.

4. **Invariants Preserved**:
   - Single database writer transaction on `state.db` before dispatch.
   - Timeouts transition order to `UNKNOWN` while preserving active risk reservation.
   - Zero automatic blind retries on transient errors.
   - Settlement atomically commits realized P&L to `state.db` and releases risk exposure.
   - Safe Stop blocks new submissions but does not stop the event pump or settlement processing for
     contracts already open.

## Validation

Local deterministic coverage resides in:

- `tests/contract/test_deriv_live_order_contract.py`
- `tests/contract/test_deriv_digit_diff_contract.py`
- `tests/unit/test_tick_ring_buffer.py`
- `tests/unit/test_deriv_tick_stream.py`
- `tests/integration/test_deriv_live_trade_lifecycle.py`
- `tests/chaos/test_deriv_live_timeout_recovery.py`

External Deriv Demo tests remain opt-in and must never contain tokens or live account identifiers in
fixtures or logs.

