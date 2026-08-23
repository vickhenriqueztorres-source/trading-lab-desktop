# IPC Protocol v1

## Scope

IPC v1 is the local framed contract between the Trading Core and isolated local processes. It now
also has an authenticated Auth Agent subprotocol. The
implemented financial worker remains explicitly `SIMULATED`. Phase 0 also includes a Deriv
read-only market-data worker; it cannot submit, modify, cancel or reconcile financial orders.

The Core remains the only financial authority. A financial worker receives an already approved
command and never receives the Core `state.db` path, Risk Ledger, strategy state, broker secret or
global balance. The simulated worker owns a separate durable synthetic broker store; the Core cannot
read or write that store and reaches it only through worker IPC. The Deriv read-only worker receives
only market-data requests over IPC and broker credentials remain confined to Deriv transport
abstractions when the future explicit demo flow is used.

## Transport and framing

- Transport: bidirectional TCP over IPv4 loopback (`127.0.0.1`) only.
- Port: the Core binds port `0`; the operating system selects a free port before the subprocess is
  spawned.
- Frame: four-byte unsigned big-endian payload length followed by UTF-8 JSON.
- Maximum payload: 65,536 bytes (`64 KiB`). This is enough for the small command/event contract
  while bounding allocation and excluding market-data bulk traffic.
- A receiver rejects an oversized length after reading only the four-byte header.
- Empty, truncated, invalid UTF-8 and invalid JSON frames are protocol errors. Partial JSON is never
  interpreted.
- Python object serialization (`pickle`, `marshal`, `eval`, `exec`) is not used.

Transport timeouts use monotonic local duration. Financial `deadline_at` remains an absolute,
timezone-aware UTC timestamp and is not inferred from a socket timeout.

## Envelope

Every message contains exactly:

```text
protocol_version
message_id
correlation_id
causation_id | null
source
target
message_type
created_at_utc
deadline_at | null
payload
```

`message_id` identifies the message. `correlation_id` is preserved from TradeIntent through outbox,
command and response. A response sets `causation_id` to the request `message_id`; routing never
depends on arrival order.

Roles are currently `CORE`, `AUTH_AGENT`, `SIMULATED_WORKER` and `DERIV_WORKER`. A role/target mismatch closes the
connection. JSON is decoded and the envelope and typed payload are validated before conversion to
domain models.

## Handshake and capabilities

The first exchange is `HELLO` followed by `HELLO_ACK`. No financial command is sent before the Core
validates:

- protocol version `1`;
- roles and correlation/causation;
- worker type `simulated`;
- practice account mode;
- products, reconciliation/quote flags, `supports_order_status_query`,
  `supports_order_events` and worker version.

An incompatible worker enters `INCOMPATIBLE`, closes the Health Gate and cannot receive an order.
Reconnect follows connect → handshake → capability validation → simulated recovery gate → `READY`.

For `DERIV_WORKER`, the Core validates broker `DERIV`, endpoint role `DERIV_WORKER`,
`connection_mode=PUBLIC_READ_ONLY`, `supports_market_data=true` and `can_submit_orders=false`.
If a worker advertises order submission capability, the generic read-only supervisor fails closed.
The Deriv worker connects and validates its read-only session before it acknowledges readiness; a
schema-incompatible startup does not become `READY`.

### Auth Agent handshake

The Auth Agent binds its own ephemeral port on `127.0.0.1`. Its supervisor passes a runtime 256-bit
session token through inherited `stdin`, never through command-line arguments, environment variables
or startup logs. The child prints only `{"port": n}` to stdout.

The first frame is `AUTH_HANDSHAKE_REQUEST` with protocol version, client version, client nonce and
the ephemeral token. The server compares the token with `hmac.compare_digest`. A valid response
contains a fresh server nonce and HMAC-SHA-256 proof over both nonces, allowing the client to prove
the peer also knows the spawn token. Wrong/missing token, expired deadline, wrong role/version or
invalid proof closes the connection. Authentication failures are bounded per process.

This authenticates possession of the spawn capability; it does not encrypt loopback traffic or yet
bind the socket to a Windows SID/signed binary. Those are release-hardening gates.

### Launcher/Core lifecycle handshake

O Launcher inicia `python -m apps.core.runner` sem segredo em argv/environment, atribui o processo
ao Windows Job Object e somente então entrega por `stdin` um token efêmero de 256 bits, perfil e
workers allowlisted. O Core host cria Auth Agent, runtime/DB/recovery e workers antes de publicar
apenas `{"port": n}`.

O primeiro frame `LIFECYCLE_HANDSHAKE_REQUEST` valida papel `LAUNCHER → CORE`, versão, deadline,
token e nonce. `LIFECYCLE_HANDSHAKE_RESPONSE` prova posse por HMAC-SHA-256 sobre nonces. O canal é
serializado, bounded e possui cache de replay 128. Ele não contém ordem, risco, saldo, banco,
market data ou credencial de broker.

## Message types

IPC v1 implements:

- `LIFECYCLE_HANDSHAKE_REQUEST`, `LIFECYCLE_HANDSHAKE_RESPONSE`;
- `CORE_LIFECYCLE_STATUS_REQUEST`, `CORE_LIFECYCLE_STATUS_RESPONSE`;
- `CORE_SAFE_STOP_REQUEST`, `CORE_SAFE_STOP_ACK`;
- `CORE_DRAIN_REQUEST`, `CORE_DRAIN_RESPONSE`;
- `CORE_WORKERS_SHUTDOWN_REQUEST`, `CORE_WORKERS_SHUTDOWN_ACK`;
- `CORE_AUTH_SHUTDOWN_REQUEST`, `CORE_AUTH_SHUTDOWN_ACK`;
- `CORE_RESTART_COMPONENT_REQUEST`, `CORE_RESTART_COMPONENT_RESPONSE`;
- `CORE_PROCESS_SHUTDOWN_REQUEST`, `CORE_PROCESS_SHUTDOWN_ACK`;
- `AUTH_HANDSHAKE_REQUEST`, `AUTH_HANDSHAKE_RESPONSE`;
- `AUTH_START_LOGIN_REQUEST`, `AUTH_START_LOGIN_RESPONSE`;
- `AUTH_SUBMIT_OTP_REQUEST`, `AUTH_SUBMIT_OTP_RESPONSE`;
- `AUTH_RENEW_REQUEST`, `AUTH_RENEW_RESPONSE`;
- `AUTH_CHECK_AUTHORIZATION_REQUEST`, `AUTH_CHECK_AUTHORIZATION_RESPONSE`;
- `AUTH_STATUS_REQUEST`, `AUTH_STATUS_RESPONSE`;
- `AUTH_SHUTDOWN_REQUEST`, `AUTH_SHUTDOWN_ACK`;
- `HELLO`, `HELLO_ACK`;
- `PING`, `PONG`;
- `ORDER_SUBMIT`, `ORDER_ACCEPTED`, `ORDER_REJECTED`, `ORDER_STATUS_UNKNOWN`;
- unsolicited `ORDER_EVENT` lifecycle notifications;
- `ORDER_STATUS_REQUEST`, `ORDER_STATUS_RESPONSE`;
- `BROKER_CAPABILITIES_REQUEST`, `BROKER_CAPABILITIES_RESPONSE`;
- `MARKET_SYMBOLS_REQUEST`, `MARKET_SYMBOLS_RESPONSE`;
- `MARKET_CONTRACTS_REQUEST`, `MARKET_CONTRACTS_RESPONSE`;
- `MARKET_TICK_SUBSCRIBE`, `MARKET_TICK`;
- `MARKET_TICK_UNSUBSCRIBE`, `MARKET_TICK_UNSUBSCRIBED`;
- `MARKET_HISTORY_REQUEST`, `MARKET_HISTORY_RESPONSE`;
- `BROKER_CLOCK_REQUEST`, `BROKER_CLOCK_RESPONSE`;
- `BROKER_BALANCE_REQUEST`, `BROKER_BALANCE_RESPONSE`;
- `WORKER_HEALTH_REQUEST`, `WORKER_HEALTH_RESPONSE`;
- `SHUTDOWN`, `SHUTDOWN_ACK`;
- `ERROR` as a reserved typed error envelope.

`ORDER_SUBMIT` carries only local order/intent IDs, broker/account/product/symbol/direction, integer
minor units, currency and deadline. It does not carry strategy internals, the Risk Ledger, balance,
database information or secrets.

Auth login requests transiently carry e-mail/OTP to the Auth Agent and are never logged or
persisted by the IPC layer. Stored authentication material never crosses back. Authorization
responses are allow/block, stable reason code and lease expiry only; status exposes a hashed user
preview, device ID and lease-active flag. The financial coordinator uses only the authorization
request. `Envelope.__repr__` always redacts payload content.

`ORDER_STATUS_REQUEST` is read-only and carries the preserved correlation/intent/order identifiers,
client order reference and the exact broker, account, product, symbol, direction, integer amount,
currency and optional known broker order ID. `ORDER_STATUS_RESPONSE` returns a typed query outcome
and, only for `FOUND`, immutable evidence with provenance, observation time, evidence version and a
stable evidence ID. External payloads are validated before domain conversion.

`ORDER_EVENT` has no causation ID because it is not a response. It carries an immutable normalized
`BrokerOrderEvent`: stable event/version IDs, broker/account/client and broker references,
correlation ID, optional external sequence, external status, occurrence/observation UTC times,
product/symbol/direction, integer amount/currency, optional settlement result and canonical evidence
hash. The Core validates the envelope and payload before the domain and durably records the inbox
event before applying any projection.

The worker's synthetic external lifecycle store and delivery counter are separate: persisting an
external occurrence does not assert that IPC delivery succeeded. The simulated scenarios may omit,
duplicate or reorder delivery without changing the external truth used by status fallback.

Market-data IPC messages are read-only. They carry broker symbols, subscription IDs, history style,
count, optional timeframe and optional positive history `end_epoch` only. They do not carry stake,
order intent, portfolio allocation, Risk Ledger state, strategy internals or broker secrets.
Normalized market responses use immutable domain models and `Decimal` for prices.

Demo account telemetry uses separate read-only messages. `BROKER_BALANCE_RESPONSE` contains only
exact integer minor units, ISO currency, `account_type=DEMO` and observation time. It never contains
account ID, token or login ID. `BROKER_CLOCK_RESPONSE` contains server epoch, local observation,
round-trip duration, derived millisecond values and synchronization status. Public-mode balance
requests fail with `DERIV_DEMO_AUTH_REQUIRED`.

For `DERIV_WORKER`, websocket responses are multiplexed internally by Deriv `req_id`; unsolicited
subscription ticks are normalized and emitted as `MARKET_TICK_EVENT` envelopes. These events have no
`causation_id` and preserve the original subscription correlation ID. The worker-side stream queue
is bounded; overflow or malformed stream data degrades market health instead of silently feeding a
strategy.

## Reconciliation and evidence rules

Recovery runs before reconciliation. The Reconciliation Coordinator then uses only the worker's
status-query port; it does not own a socket, access the simulated broker store or call order submit.
Timeout and unavailable results may retry the read-only query under a bounded policy. No outcome of
a status query can enqueue or resend `ORDER_SUBMIT`.

Evidence must match the durable Core record on client reference, broker, account, product, symbol,
direction, integer amount and currency, plus broker order ID when both sides have one. A mismatch is
persisted as a conflict for manual review and leaves the order ambiguous with exposure active.
Repeated identical evidence is idempotent; conflicting evidence cannot regress an already resolved
order.

The Core commits evidence, reconciliation attempt, order state/provenance, outbox state, reservation
release and realized P&L in one SQLite transaction. Accepted/open evidence keeps exposure active.
Rejected/settled evidence releases it once. Settlement-unknown evidence remains active exposure.
`NOT_FOUND`, timeout, unavailable, malformed data or elapsed time never prove rejection and never
release exposure. An ambiguous outbox becomes `RECONCILED` only on sufficient evidence and never
returns to `PENDING`.

## Deadline and delivery classification

The worker validates the command deadline immediately before the simulated send boundary. If it can
prove the boundary was not crossed, it returns `ORDER_REJECTED` with `ORDER_COMMAND_EXPIRED`.

The Core classifies dispatch as:

```text
NOT_SENT
POSSIBLY_SENT
RESPONSE_RECEIVED
```

- `NOT_SENT` becomes `Outbox=BLOCKED_NOT_SENT`, `Order=SEND_BLOCKED`; reservation remains active and
  there is no automatic retry.
- `POSSIBLY_SENT` becomes `Outbox=AMBIGUOUS`, `Order=UNKNOWN`; reservation remains active and the
  Health Gate closes.
- Proven acceptance/rejection is persisted by the Core in one writer transaction. A proven
  rejection releases the reservation according to the existing ledger rules.

A `sendall` failure is potentially partial and is never classified as proof of non-delivery. Worker
restart never requeues an `AMBIGUOUS` item.

## Replay, queues and lifecycle

The client keeps a bounded replay window. An identical `message_id` and body is idempotent; the same
ID with different content is `IPC_MESSAGE_REPLAY_CONFLICT` and degrades the worker.

Request and event queues are bounded. Queue saturation is `IPC_BACKPRESSURE`, degrades health and
blocks new entries; a financial event is not silently discarded. The reader supports unsolicited
events through envelope correlation rather than assuming strict request/response arrival order.

Domain-event idempotency is durable and distinct from the transport replay window. `event_id` is
unique in migration `0004_broker_order_events`; identical replay has no repeated effect, while the
same ID with a different evidence hash is a persisted conflict. Matching is strict across broker,
account, references, correlation, product, symbol, direction, amount and currency. Event inbox,
order transition, sequence/provenance, reservation release and realized P&L commit atomically under
the single writer. P&L and release counters prove at-most-once effects.

The normal simulated sequence is `ACCEPTED → OPEN → SETTLED`. Missing or increasing sequence gaps
close the affected broker/account Health Gate and invoke only the read-only status-query fallback;
they never submit an order. Late events cannot regress a terminal order, `SETTLEMENT_UNKNOWN`
retains exposure, and startup reconciliation includes accepted/open orders so a lost settlement can
be recovered after Core and worker restart.

Worker health states are `STARTING`, `HANDSHAKING`, `READY`, `DEGRADED`, `DISCONNECTED`,
`INCOMPATIBLE` and `STOPPED`. Heartbeat detects a dead connection but never infers an order result.
Restart uses bounded exponential backoff and a `CLOSED`/`OPEN`/`HALF_OPEN` process circuit breaker.
Graceful shutdown uses `SHUTDOWN_ACK`; timeout escalates to terminate and then kill.

O Launcher aplica `CORE_SAFE_STOP → CORE_DRAIN → CORE_WORKERS_SHUTDOWN → CORE_AUTH_SHUTDOWN →
CORE_PROCESS_SHUTDOWN`. Drain observa somente a fila bounded e o evento em persistência; não espera
settlement futuro. O event pump permanece ativo enquanto o Simulated Worker encerra e o writer só
fecha no último passo. Timeout escala para terminate/kill, e o Job Object contém descendentes.

Restart lifecycle é allowlisted apenas para `AUTH_AGENT` e `DERIV_WORKER`. O Launcher nunca troca o
Simulated Worker financeiro sob um Core ativo: sua queda mantém `DEGRADED` e exige novo startup com
recovery/reconciliação. Queda do Core termina a árvore inteira.

The Auth Agent has its own bounded replay cache (128 responses) and rejects conflicting reuse of a
message ID. Its health states are `STARTING`, `HANDSHAKING`, `READY`, `UNAVAILABLE` and `STOPPED`.
Status heartbeat detects process/connection loss. During `UNAVAILABLE`, authorization returns
`HG_AUTH_AGENT_UNAVAILABLE`; no `TradeIntent` is created, while financial event processing and
reconciliation remain independent. Explicit restart uses bounded exponential delay, rotates the
session token, reopens the DPAPI vault and revalidates the signed lease before a new entry can pass.

Market data health is separate from financial order state. A local monotonic suspension gap marks
Deriv market data as `STALE`, moves active subscriptions into restoring state and requires
read-only resynchronization/backfill before the worker reports healthy data again.

Core-side recovery uses a per-series generation. Reconnect, restart, suspension, gap and
backpressure remain blocked until bounded history overlap and continuity are proven. An old
generation response cannot reopen the current Market Health Gate.

The Core continuous-shadow composition uses the existing read-only worker supervisor and replaces
the IPC client after process loss. A worker reaching IPC `READY` is not sufficient: explicit shadow
recovery rebuilds the read-only pump/scheduler, validates overlap for the current generation and
only then restores tick subscription. Local contract tests kill the fake Deriv subprocess and
prove the replacement PID cannot deliver strategy candles before this sequence completes.

The caller-driven shadow host bounds total poll/recovery actions per cycle and applies a monotonic
per-series restart circuit. One degraded series does not consume all cycle actions or reopen another
series. CPU/RSS/lag budget exhaustion shuts down shadow services; it never emits a financial IPC
message.

For shared Deriv market-data sessions, the Core now inserts a bounded tick router between the single
`SocketWorkerClient` event queue and per-series `ContinuousShadowRuntime` instances. The router
maps `MARKET_TICK` events by subscription id and validates broker/symbol before a tick can reach a
series aggregator. Unknown subscription or scope mismatch is `MD_SCOPE_MISMATCH`; saturated
per-series queues are `MD_BACKPRESSURE`. This is market-data-only routing and does not create an
order command, account credential path or financial retry.

`BrokerShadowSession` owns one read-only supervisor/client for a broker and rebuilds the shared
router plus per-series runtimes after a single `restart()`. Worker loss marks every subscribed
series as reconnecting through the existing runtime contract; recovery restores each subscription
only after the series-level scheduler/backfill proves health. The session never retries financial
submission because it has no financial command surface.

`BrokerShadowSoakRunner` adds a bounded Core-side soak harness around that broker-level session. It
samples Core resources and child worker PID/RSS, injects local crash/suspension scenarios in tests,
and performs explicit recovery only inside configured limits. Resource exhaustion or recovery-limit
exhaustion shuts down the read-only session and emits operational reason codes; it does not add any
IPC command type, financial retry, broker credential path, or order reconciliation shortcut.

`BrokerShadowTemporalSoakRunner` turns that harness into a controlled monotonic window with maximum
cycle bounds, explicit acceptance criteria and a JSON-safe report. The report includes summarized
worker health, resource samples, subscriptions and reason codes only; it does not persist raw market
payloads, broker credentials, financial commands or reconciliation evidence.

`BrokerShadowTemporalSoakMatrixRunner` compares a bounded set of local temporal scenarios without
adding an IPC message type. Scenario identifiers are validated and unique, failures do not prevent
later scenarios from running, and unexpected exceptions are reduced to a stable reason code after
read-only shutdown. A shutdown failure receives its own stable code without serializing the raw
exception, and later scenarios still run. The combined JSON report only nests the already-redacted
temporal reports.

## Stable errors

Implemented stable codes include:

```text
IPC_PROTOCOL_INCOMPATIBLE
IPC_FRAME_TOO_LARGE
IPC_FRAME_TRUNCATED
IPC_INVALID_FRAME
IPC_INVALID_JSON
IPC_INVALID_ENVELOPE
IPC_UNKNOWN_MESSAGE_TYPE
IPC_CONNECTION_LOST
IPC_HANDSHAKE_TIMEOUT
IPC_ROLE_MISMATCH
IPC_MESSAGE_REPLAY_CONFLICT
IPC_BACKPRESSURE
AUTH_IPC_AUTHENTICATION_FAILED
AUTH_IPC_UNAVAILABLE
AUTH_IPC_REQUEST_TIMEOUT
AUTH_IPC_INVALID_MESSAGE
AUTH_IPC_DUPLICATE_CONFLICT
WORKER_NOT_READY
WORKER_CRASHED
ORDER_COMMAND_EXPIRED
ORDER_DISPATCH_AMBIGUOUS
RECONCILIATION_NOT_FOUND
RECONCILIATION_UNAVAILABLE
RECONCILIATION_QUERY_TIMEOUT
RECONCILIATION_INVALID_RESPONSE
RECONCILIATION_CONFLICT
RECONCILIATION_EVIDENCE_CONFLICT
BROKER_ORDER_ID_CONFLICT
BROKER_EVENT_REPLAY_CONFLICT
BROKER_EVENT_SCOPE_MISMATCH
BROKER_EVENT_ACCOUNT_MISMATCH
BROKER_EVENT_STATE_CONFLICT
BROKER_EVENT_SETTLEMENT_CONFLICT
BROKER_EVENT_RESULT_CURRENCY_MISMATCH
BROKER_EVENT_SEQUENCE_CONFLICT
ORDER_EVENT_SEQUENCE_GAP
WORKER_CAPABILITY_DENIED
DERIV_SCHEMA_INCOMPATIBLE
DERIV_TRADING_OPERATION_DISABLED
DERIV_REAL_ACCOUNT_FORBIDDEN
DERIV_REAL_WS_FORBIDDEN
DERIV_DEMO_AUTH_REQUIRED
DERIV_DEMO_REAUTH_REQUIRED
DERIV_BALANCE_UNAVAILABLE
DERIV_BALANCE_PRECISION_UNSUPPORTED
DERIV_ACCOUNT_EVENT_BACKPRESSURE
```
