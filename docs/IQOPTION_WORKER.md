# IQ Option Worker (Subprocesso Isolado Practice / Demo)

## 1. Visão Geral e Arquitetura

O **IQ Option Worker** (`apps/iqoption_worker/` e `packages/brokers/iqoption/`) é o componente responsável pela integração, execução de ordens e reconciliação com a IQ Option.

Ele opera como um subprocesso independente, supervisionado pelo Windows Job Object do Launcher e conectado ao Trading Core via protocolo IPC v1 autenticado sobre TCP loopback (`FramedSocket`).

```text
Launcher (Windows Job Object)
├── Trading Core (state.db)
│     ├── OrderCoordinator
│     └── ReconciliationCoordinator
├── Deriv Worker (Processo Isolado)
└── IQ Option Worker (Processo Isolado — Practice)
      ├── IQOptionWorkerServer (IPC v1 / Port negociada)
      ├── IQOptionOrderSession (Submissão & Streaming)
      ├── IQOptionReconciliationHandler (Reconciliação Autoritativa)
      └── IQOptionPracticeSession (Sessão WebSocket / Fake)
```

---

## 2. Invariantes e Segurança

1. **Guarda Anti-Conta Real Inviolável (`AG-INV-006 / R-RISK-009`)**:
   - O worker valida rigorosamente o tipo de conta (`balance_type == 4` / `is_demo == True`).
   - Qualquer indicação de conta real (`balance_type == 1` ou `account_type == "real"`) falha imediatamente levantando `IQOPTION_REAL_ACCOUNT_FORBIDDEN` antes de qualquer requisição.
2. **Isolamento de Falha (`R-ARCH-007 / R-BRK-001`)**:
   - O Core e o Deriv Worker **não importam** nenhum módulo da IQ Option.
   - Uma queda, crash ou desconexão do IQ Option Worker nunca degrada o Core ou a Deriv.
3. **Persistência Antes do Despacho (`AG-INV-001`)**:
   - `TradeIntent`, `RiskReservation`, `Order` e `OutboxMessage` são persistidos atomicamente no SQLite `state.db` antes do comando IPC `ORDER_SUBMIT`.
4. **Sem Retries Automáticos Cegos (`AG-INV-002`)**:
   - Qualquer timeout de envio transiciona a ordem para `UNKNOWN` preservando a reserva de risco. A resolução ocorre exclusivamente via `ReconciliationCoordinator`.

---

## 3. Mensagens e Capacidades IPC

O servidor anuncia as seguintes capacidades no handshake `HELLO_ACK`:
```json
{
  "broker": "IQOPTION",
  "account_modes": ["practice"],
  "products": ["BINARY_OPTION", "DIGITAL_OPTION", "OPTIONS"],
  "supports_reconciliation": true,
  "supports_quotes": true,
  "supports_order_status_query": true,
  "supports_order_events": true,
  "worker_version": "0.4.0",
  "can_submit_orders": true,
  "supports_market_data": true,
  "connection_mode": "PRACTICE"
}
```

### Comandos Suportados
- `HELLO` $\rightarrow$ `HELLO_ACK` (Handshake de protocolo)
- `ORDER_SUBMIT` $\rightarrow$ `ORDER_ACCEPTED` / `ORDER_REJECTED`
- `ORDER_STATUS_REQUEST` $\rightarrow$ `ORDER_STATUS_RESPONSE` (Reconciliação)
- `BROKER_BALANCE_REQUEST` $\rightarrow$ `BROKER_BALANCE_RESPONSE`
- `BROKER_CLOCK_REQUEST` $\rightarrow$ `BROKER_CLOCK_RESPONSE`
- `BROKER_CAPABILITIES_REQUEST` $\rightarrow$ `BROKER_CAPABILITIES_RESPONSE`
- `SHUTDOWN` $\rightarrow$ `SHUTDOWN_ACK`

### Streaming de Eventos
- `ORDER_EVENT`: envelopes assíncronos contendo `BrokerOrderEvent` com status `OPEN` ou `SETTLED` (incluindo P&L realizado em unidades inteiras menores e `evidence_hash`).
