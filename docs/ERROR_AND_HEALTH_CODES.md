# Reason Codes e Health Gates

## 1. Contrato

Reason codes são identificadores estáveis para testes, UI futura, suporte e auditoria. Mensagens
humanas podem mudar; código não deve ser renomeado silenciosamente. O enum/módulo de origem é
autoritativo.

Este documento agrupa famílias; não substitui a enumeração do código.

## 2. Fontes autoritativas

- protocolo: `packages/protocol/errors.py`;
- database health: `packages/persistence/health.py`;
- Core Health Gate: `apps/core/health.py` e chamadores;
- auth/licença: `apps/auth_agent/`, `packages/licensing/`;
- vault/DPAPI: `packages/security/dpapi.py`, `packages/security/windows_vault.py`;
- market data: `packages/market_data/`, `packages/market_pipeline/`;
- strategy/catalog: `packages/strategy_catalog/`, `packages/strategies/`;
- shadow/soak: `apps/core/shadow_*`, `apps/core/broker_shadow_*`;
- Deriv: `apps/deriv_worker/`, `packages/brokers/deriv/`.

## 3. Convenções

| Prefixo | Significado |
|---|---|
| `HG_` | motivo que bloqueia Health Gate/novas entradas |
| `DB_` | saúde/persistência crítica |
| `IPC_` | framing, envelope, handshake ou transporte |
| `WORKER_` | lifecycle/capability do worker |
| `BROKER_EVENT_` | validação/aplicação de evento financeiro |
| `RECONCILIATION_` | consulta/evidência de reconciliação |
| `DERIV_` | adapter/worker Deriv |
| `MD_` | market pipeline/health por série |
| `CANDLE_` | ingresso/persistência/commit de candle |
| `CHECKPOINT_`/`JOURNAL_` | replay/evidência |
| `SHADOW_`/`BROKER_SHADOW_` | runtime/host/session/soak read-only |
| `SOAK_CLI_` | opt-in, argumentos e falha operacional da CLI local |
| `SOAK_FAULT_` | injeção/observação/recovery de fault preset local |
| `ATOMIC_JSON_`/`REPORT_RETENTION_` | publicação e retenção de relatórios locais |
| `SECRET_SCAN_` | varredura bounded de segredo local |
| `VAULT_` | plataforma, DPAPI, ACL, integridade e persistência do vault local |
| `AUTH_` | sessão/PKCE/device/token |
| `AUTH_IPC_` | autenticação, disponibilidade, timeout e replay do Auth Agent IPC |
| `LIFECYCLE_IPC_` | autenticação, disponibilidade, timeout e replay Launcher/Core |
| `LAUNCHER_` | lock, startup, árvore e shutdown do Launcher |
| `PORTFOLIO_`/`STRATEGY_` | estratégia/allocator/catalog |

## 4. Health Gate financeiro

Exemplos:

- `HG_SAFE_STOP` — novas entradas paradas explicitamente;
- `HG_ORDER_UNKNOWN` — submissão ambígua;
- `HG_SETTLEMENT_UNKNOWN` — liquidação não comprovada;
- `HG_RECONCILIATION_REQUIRED` — estado não terminal exige consulta;
- `HG_RECONCILIATION_CONFLICT` — evidências inconsistentes;
- `DERIV_RECONCILIATION_AMBIGUOUS_MATCH` — mais de um contrato externo coincide com a janela e os
  campos financeiros; a reserva permanece ativa para revisão segura;
- `HG_WORKER_DISCONNECTED` — worker indisponível;
- `HG_WORKER_CIRCUIT_OPEN` — restart rápido suspenso;
- `HG_ORDER_EVENT_GAP` — sequência financeira incompleta;
- `HG_ORDER_EVENT_CONFLICT` — evento incompatível.

Esses gates não devem ser limpos por tempo decorrido. A reabertura exige recovery/evidência válida.

### 4.1 Governança e Escopo Multi-Corretora (Cross-Broker Isolation)

O `HealthGate` opera em dois níveis estritamente isolados:
1. **Nível Global**: Bloqueios que afetam o ecossistema inteiro (`HG_SAFE_STOP`, `DB_WRITE_FAILED`, `HG_AUTH_AGENT_UNAVAILABLE`, `HG_LEASE_EXPIRED`, `HG_DAILY_STOP_REACHED`, `HG_COOLDOWN_ACTIVE`). Qualquer falha neste nível bloqueia novas entradas em todas as corretoras simultaneamente.
2. **Nível Escopado `(broker, account_id)`**: Bloqueios restritos a uma corretora e conta específica (`HG_WORKER_DISCONNECTED`, `HG_WORKER_NOT_READY`, `HG_ORDER_UNKNOWN`). O escopo especial `(broker, market-data)` é broker-wide e participa do gate de todas as contas financeiras desse broker, sem contaminar outro broker.
   - A degradação, desconexão ou timeout na IQ Option bloqueia novas entradas **apenas** na IQ Option (`HG_WORKER_DISCONNECTED`), mantendo a Deriv aberta.
   - A degradação na Deriv bloqueia apenas a Deriv, mantendo a IQ Option aberta.
   - O Core avalia `can_enter_order(broker, account_id)` exigindo que tanto o gate global quanto o gate do escopo estejam abertos.

### 4.2 Gestão de Risco Consolidada e Limites Globais

- `HG_GLOBAL_EXPOSURE_EXCEEDED` — a soma das reservas ativas e ordens abertas cross-broker excede o teto global;
- `HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED` — a exposição somada em um mesmo ativo canônico cross-broker (ex: EURUSD) excede o teto do ativo;
- `HG_DAILY_STOP_REACHED` — o P&L realizado diário consolidado de todas as corretoras atingiu o stop loss máximo, bloqueando novas entradas globalmente (`RISK_LOCKED`);
- `HG_DAILY_TAKE_PROFIT_REACHED` — a meta diária configurada para `DIGITDIFF` foi atingida; novas entradas ficam bloqueadas para preservar o lucro já realizado;
- `HG_COOLDOWN_ACTIVE` — sequência máxima de perdas consecutivas atingida, ativando pausa. A origem UTC e a duração ficam em `state.db`; após restart o Core reconstrói o restante e usa `time.monotonic()` somente para a espera dentro do processo atual.

`DERIV_READY_TO_ARM` é uma projeção explicativa da UI, não um blocker financeiro novo. Pode exibir
`BROKER_PROCESS_NOT_READY`, `BROKER_NOT_AUTHENTICATED`, `RECONCILIATION_INCOMPLETE`,
`RISK_NOT_READY`, `CLOCK_NOT_TRUSTED`, `MARKET_NOT_HEALTHY` ou `WARMUP_INCOMPLETE`.

Os blockers de Stop Loss e Take Profit não são limpos por edição da configuração durante o mesmo
dia. O reset diário explícito zera P&L/contadores e limpa as travas. O cooldown não cancela nem
interrompe a liquidação de contratos abertos.

Validação de configuração de dígitos pode responder com os códigos estáveis
`DIGIT_RISK_STAKE_BELOW_MINIMUM`, `DIGIT_RISK_STOP_LOSS_INVALID`,
`DIGIT_RISK_TAKE_PROFIT_INVALID`, `DIGIT_RISK_CONSECUTIVE_LOSSES_INVALID`,
`DIGIT_RISK_COOLDOWN_INVALID`, `DIGIT_RISK_CONFIDENCE_INVALID`,
`DIGIT_RISK_SYMBOL_NOT_ALLOWED` e `DIGIT_RISK_CURRENCY_NOT_SUPPORTED`.

## 5. Identidade/licença

Exemplos:

- `HG_AUTH_REQUIRED`;
- `HG_LEASE_EXPIRED`;
- `HG_LEASE_REVOKED`;
- `HG_LEASE_INVALID_SIGNATURE`;
- `HG_LEASE_DEVICE_MISMATCH`;
- `HG_ENTITLEMENT_MISSING`;
- `HG_CLIENT_INCOMPATIBLE`;
- `HG_REAL_MODE_DISABLED`.
- `HG_AUTH_AGENT_UNAVAILABLE`.

Eles bloqueiam novas entradas e não interrompem ordens abertas.

Vault local:

- `VAULT_PLATFORM_UNSUPPORTED` — DPAPI requisitado fora do Windows;
- `VAULT_ENCRYPTION_FAILED` — DPAPI não comprovou proteção;
- `VAULT_DECRYPTION_FAILED` — usuário/entropia/blob não pôde ser autenticado;
- `VAULT_CONFIGURATION_INVALID` — diretório/chave fora do contrato;
- `VAULT_ACL_FAILED` — DACL restrita ao SID atual não foi aplicada;
- `VAULT_STORAGE_FAILED` — leitura, escrita, replace ou remoção falhou;
- `VAULT_INTEGRITY_FAILED` — envelope/pacote truncado, divergente ou adulterado.

Esses códigos não carregam segredo, caminho, SID ou payload. Falha do vault bloqueia a operação de
identidade correspondente e nunca autoriza fallback silencioso no Windows.

Auth Agent IPC:

- `AUTH_IPC_AUTHENTICATION_FAILED`;
- `AUTH_IPC_UNAVAILABLE`;
- `AUTH_IPC_REQUEST_TIMEOUT`;
- `AUTH_IPC_INVALID_MESSAGE`;
- `AUTH_IPC_DUPLICATE_CONFLICT`.

Launcher/Core lifecycle:

- `LIFECYCLE_IPC_AUTHENTICATION_FAILED`;
- `LIFECYCLE_IPC_UNAVAILABLE`;
- `LIFECYCLE_IPC_REQUEST_TIMEOUT`;
- `LIFECYCLE_IPC_INVALID_MESSAGE`;
- `LIFECYCLE_IPC_DUPLICATE_CONFLICT`;
- `LAUNCHER_INSTANCE_ALREADY_RUNNING`;
- `LAUNCHER_INSTANCE_LOCK_FAILED`;
- `CORE_STARTUP_INVALID` / `CORE_PROCESS_START_FAILED`.

Falha lifecycle não classifica ordem nem limpa exposição. O Launcher escala o encerramento da árvore
e o próximo Core executa recovery/reconciliação.

## 6. Estratégias

Exemplos:

- `HG_STRATEGY_NOT_FOUND`;
- `HG_STRATEGY_HASH_MISMATCH`;
- `HG_STRATEGY_INCOMPATIBLE`;
- `HG_STRATEGY_NOT_RELEASED`;
- `HG_STRATEGY_SUSPENDED`;
- `HG_STRATEGY_RETIRED`;
- `HG_STRATEGY_VALIDATION_INCOMPLETE`;
- `PORTFOLIO_BUDGET_EXCEEDED`;
- `PORTFOLIO_CURRENCY_MISMATCH`;
- `OPPOSING_SIGNALS_CANCELLED`;
- `CONSENSUS_NO_STAKE_SUM`.

## 7. Persistência

Exemplos:

- `DB_NOT_CHECKED`;
- `DB_OPEN_FAILED`;
- `DB_LOCK_FAILED`;
- `DB_MISSING_UNEXPECTED`;
- `DB_INTEGRITY_FAILED`;
- `DB_MIGRATION_FAILED`;
- `DB_WRITE_FAILED`;
- `MIGRATION_CHECKSUM_MISMATCH`;
- `STRATEGY_DATA_INTEGRITY_FAILED`;
- `STRATEGY_DATA_MIGRATION_FAILED`.

Falha crítica mantém novas entradas bloqueadas.

## 8. IPC e worker

`ProtocolErrorCode` inclui framing, JSON/envelope, versão, role, replay, backpressure, disconnect,
deadline, ambiguity, reconciliação, broker events e guards Deriv. Exemplos:

- `IPC_FRAME_TOO_LARGE`;
- `IPC_INVALID_JSON`;
- `IPC_PROTOCOL_INCOMPATIBLE`;
- `IPC_HANDSHAKE_TIMEOUT`;
- `IPC_MESSAGE_REPLAY_CONFLICT`;
- `IPC_BACKPRESSURE`;
- `ORDER_COMMAND_EXPIRED`;
- `ORDER_DISPATCH_AMBIGUOUS`;
- `WORKER_CAPABILITY_DENIED`.

## 9. Market data

Exemplos:

- `MD_INITIAL_WARMUP`;
- `MD_HEALTHY`;
- `MD_GAP_DETECTED`;
- `MD_SOURCE_STALE`;
- `MD_CLOCK_UNTRUSTED`;
- `MD_RECONNECT_REQUIRED`;
- `MD_CONTINUITY_UNPROVEN`;
- `MD_BACKPRESSURE`;
- `MD_SCOPE_MISMATCH`;
- `MD_SHADOW_DIVERGENCE`;
- `MD_STORAGE_FAILED`.

Somente `MD_HEALTHY` permite delivery shadow; isso não autoriza dispatch financeiro.

## 10. Deriv market data e execução Demo

Exemplos:

- `DERIV_TRADING_OPERATION_DISABLED`;
- `DERIV_REAL_ACCOUNT_FORBIDDEN`;
- `DERIV_REAL_WS_FORBIDDEN`;
- `DERIV_WS_HOST_FORBIDDEN`;
- `DERIV_WS_PATH_FORBIDDEN`;
- `DERIV_DEMO_OTP_MISSING`;
- `DERIV_DEMO_AUTH_REQUIRED`;
- `DERIV_DEMO_REAUTH_REQUIRED`;
- `DERIV_TELEMETRY_UNAVAILABLE`;
- `DERIV_TICK_STREAM_DISCONNECTED`;
- `DERIV_SUBSCRIPTION_DISCONNECTED`;
- `DERIV_DIGIT_BARRIER_REQUIRED`;
- `DERIV_DIGIT_CONTRACT_UNSUPPORTED`;
- `DERIV_BALANCE_UNAVAILABLE`;
- `DERIV_BALANCE_PRECISION_UNSUPPORTED`;
- `DERIV_ACCOUNT_EVENT_BACKPRESSURE`;
- `DERIV_OPERATION_NOT_ALLOWLISTED`;
- `DERIV_SCHEMA_INCOMPATIBLE`;
- `DERIV_REQUEST_TIMEOUT`;
- `DERIV_RATE_LIMITED`;
- `DERIV_MARKET_EVENT_BACKPRESSURE`;
- `DERIV_CANDLE_BATCH_OVERFLOW`;
- `DERIV_CANDLE_HISTORY_SCOPE_MISMATCH`.

## 11. Shadow e soak

Exemplos:

- `SHADOW_WORKER_NOT_READY`;
- `SHADOW_POLL_FAILED`;
- `SHADOW_RECOVERY_FAILED`;
- `SHADOW_CPU_LIMIT_EXCEEDED`;
- `SHADOW_RSS_LIMIT_EXCEEDED`;
- `SHADOW_LAG_LIMIT_EXCEEDED`;
- `BROKER_SHADOW_SOAK_RECOVERY_LIMIT_EXCEEDED`;
- `BROKER_SHADOW_TEMPORAL_SOAK_DURATION_NOT_REACHED`;
- `BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_FAILED`;
- `BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_RAISED`;
- `BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED`.

CLI e persistência do relatório:

- `SOAK_CLI_OPT_IN_REQUIRED`;
- `SOAK_CLI_ARGUMENT_INVALID`;
- `SOAK_CLI_OPERATION_FAILED`;
- `ATOMIC_JSON_WRITE_FAILED`;
- `REPORT_RETENTION_FAILED`;
- `REPORT_RETENTION_SYMLINK_FORBIDDEN`;
- `REPORT_RETENTION_SCOPE_MISMATCH`.

Perfis e scanner:

- `SOAK_FAULT_WORKER_LOSS_INJECTED`;
- `SOAK_FAULT_SUSPENSION_GAP_INJECTED`;
- `SOAK_FAULT_BACKPRESSURE_INJECTED`;
- `SOAK_FAULT_BACKPRESSURE_OBSERVED`;
- `SOAK_FAULT_BACKPRESSURE_NOT_OBSERVED`;
- `SOAK_FAULT_RECOVERY_CONFIRMED`;
- `SOAK_FAULT_RECOVERY_UNCONFIRMED`;
- `SECRET_SCAN_FAILED`.

## 12. Regras para adicionar código

1. escolha família/prefixo correto;
2. use enum quando a família tiver enum autoritativo;
3. não inclua segredo/ID dinâmico no código;
4. documente semântica e owner;
5. teste caminho que produz e caminho que limpa o bloqueio;
6. não reutilize código com significado diferente;
7. preserve compatibilidade de UI/suporte;
8. registre decisão no worklog quando estrutural.
