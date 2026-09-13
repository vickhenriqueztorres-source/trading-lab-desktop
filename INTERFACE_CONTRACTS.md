# Contratos de Interface — Trading Lab Desktop

**Baseline:** v1.9.11  
**Status:** Canônico e Obrigatório  
**Data:** 2026-09-11  
**Escopo:** Todos os módulos (`apps/`), pacotes (`packages/`), protocolos IPC e agentes de IA que operam neste repositório.

---

## 1. Visão Geral e Princípios

Este documento define formalmente os **contratos de interface públicos** entre módulos, pacotes e subprocessos do **Trading Lab Desktop**. Qualquer inteligência artificial ou engenheiro de software que atue neste projeto **DEVE** gerar código que respeite rigorosamente estes contratos, limites de processo e invariantes de segurança.

### 1.1 Invariantes Fundamentais e Regras Arquiteturais

1. **Separação de Processos (R-ARCH-001):** O sistema opera em processos isolados para `Launcher`, `UI`, `Trading Core`, `Deriv Worker`, `IQ Option Worker` e `Auth Agent`.
2. **Autoridade Única Financeira (R-ARCH-002, AG-INV-004):** O `Trading Core` é a única autoridade sobre o estado financeiro local. UI, estratégias e workers **NUNCA** gravam no banco SQLite nem alteram o `RiskLedger` diretamente.
3. **Isolamento de Workers (R-ARCH-003, AG-INV-007):** Workers atuam como adaptadores de protocolo de rede/WebSocket. Eles **NÃO** executam estratégias, não calculam stake final, não tomam decisões financeiras e não contaminam o Core com bibliotecas externas de corretoras.
4. **Isolamento da UI (R-ARCH-004):** A UI (PySide6) **NUNCA** chama APIs de corretoras nem acessa diretamente o banco SQLite. Toda comunicação é intermediada via IPC contra o `UiService` do Core.
5. **Comunicação Inter-Processos Estruturada (R-ARCH-008):** Todo tráfego IPC utiliza o protocolo canônico `IPC v1` com envelopes tipados (`Envelope`), delimitados por tamanho (`Framing`), versionados e com deadlines explícitos.
6. **Integridade Financeira e Dinheiro Seguro (AG-INV-010):** É **terminantemente proibido o uso de `float`** para valores monetários, exposição, stake ou P&L. Valores utilizam inteiros (*minor units*, ex: centavos) ou `Money(minor_units, currency)`.
7. **Tratamento de Incerteza (AG-INV-002, AG-INV-003):** Se uma ordem entrar em estado `UNKNOWN` ou `SETTLEMENT_UNKNOWN`, é **proibido** tentar reenvio automático sem reconciliação prévia. O montante comprometido continua reservado como **exposição total de risco**.
8. **Segurança e Segredos (AG-INV-008, AG-INV-013):** Credenciais, tokens e senhas nunca trafegam em logs, nem viajam para o plano de controle de licença, sendo armazenados localmente no usuário Windows via DPAPI (`WindowsUserScopedVault`).

---

## 2. Protocolo de Comunicação IPC v1 (`packages/protocol`)

A comunicação entre os processos do Trading Lab Desktop ocorre exclusivamente através de sockets TCP locais (loopback `127.0.0.1`) com mensagens serializadas em JSON e delimitadas por framing de 4 bytes (big-endian).

### 2.1 Estrutura do Envelope (`Envelope`)

Localizado em `packages/protocol/envelope.py`:

```python
@dataclass(frozen=True, slots=True)
class Envelope:
    protocol_version: str        # Ex: "1.0.0" (PROTOCOL_VERSION)
    message_id: str              # UUID único da mensagem
    correlation_id: str          # ID de correlação para rastreamento de causa/efeito
    message_type: MessageType    # Tipo formal da mensagem (StrEnum)
    sender_role: EndpointRole    # Papel do remetente (LAUNCHER, CORE, UI, etc.)
    target_role: EndpointRole    # Papel do destinatário
    timestamp: datetime          # UTC timestamp de envio
    deadline_ms: int             # Limite de processamento em ms (ex: 5000)
    payload: Mapping[str, object]# Conteúdo de dados da mensagem
```

### 2.2 Papéis de Endpoint (`EndpointRole`)

| Role | Responsabilidade |
|---|---|
| `LAUNCHER` | Supervisor de processos pai, watchdog e gerenciador de updates |
| `CORE` | Trading Core: único escritor financeiro, validador de risco e orquestrador |
| `UI` | Interface de usuário (PySide6), consumidora de projeções |
| `AUTH_AGENT` | Gestor de identidade local, tokens DPAPI e leases offline |
| `DERIV_WORKER` | Worker de rede WebSocket para Deriv |
| `IQOPTION_WORKER` | Worker de rede para IQ Option |
| `SIMULATED_WORKER`| Worker determinístico para testes de caos/resiliência |

### 2.3 Tipos Canônicos de Mensagem (`MessageType`)

#### A. Canal UI ↔ Core
- `UI_HANDSHAKE_REQUEST` / `UI_HANDSHAKE_RESPONSE`: Conexão inicial e negociação de capacidades.
- `UI_PROJECTION_REQUEST` / `UI_PROJECTION_SNAPSHOT`: Solicitação e envio de snapshot de dados (saldo, posições, métricas de risco, radar).
- `UI_SAFE_STOP_COMMAND` / `UI_SAFE_STOP_ACK`: Ordem de parada segura (não abre novas posições; liquida ou aguarda abertas).
- `UI_RESUME_COMMAND` / `UI_RESUME_ACK`: Retomada operacional de bot pausado.
- `UI_UPDATE_DIGIT_RISK_CONFIG_COMMAND` / `UI_UPDATE_DIGIT_RISK_CONFIG_ACK`: Atualização da parametrização de risco de dígitos da Deriv.
- `UI_UPDATE_IQOPTION_RISK_CONFIG_COMMAND` / `UI_UPDATE_IQOPTION_RISK_CONFIG_ACK`: Atualização de parâmetros de risco/estratégia da IQ Option.
- `UI_IQOPTION_BOT_CONTROL_COMMAND` / `UI_IQOPTION_BOT_CONTROL_ACK`: Ligar/Desligar robô autônomo da IQ Option (`ARMED` / `PAUSED`).
- `UI_GENERATE_DIAGNOSTIC_COMMAND` / `UI_GENERATE_DIAGNOSTIC_RESPONSE`: Extração de pacote ZIP de diagnóstico sanitizado.

#### B. Canal Launcher ↔ Core (Lifecycle)
- `LIFECYCLE_HANDSHAKE_REQUEST` / `LIFECYCLE_HANDSHAKE_RESPONSE`
- `CORE_LIFECYCLE_STATUS_REQUEST` / `CORE_LIFECYCLE_STATUS_RESPONSE`
- `CORE_SAFE_STOP_REQUEST` / `CORE_SAFE_STOP_ACK`
- `CORE_DRAIN_REQUEST` / `CORE_DRAIN_RESPONSE`
- `CORE_WORKERS_SHUTDOWN_REQUEST` / `CORE_WORKERS_SHUTDOWN_ACK`
- `CORE_PROCESS_SHUTDOWN_REQUEST` / `CORE_PROCESS_SHUTDOWN_ACK`
- `CORE_RESTART_COMPONENT_REQUEST` / `CORE_RESTART_COMPONENT_RESPONSE`

#### C. Canal Core ↔ Auth Agent
- `AUTH_HANDSHAKE_REQUEST` / `AUTH_HANDSHAKE_RESPONSE`
- `AUTH_START_LOGIN_REQUEST` / `AUTH_START_LOGIN_RESPONSE`
- `AUTH_SUBMIT_OTP_REQUEST` / `AUTH_SUBMIT_OTP_RESPONSE`
- `AUTH_RENEW_REQUEST` / `AUTH_RENEW_RESPONSE`
- `AUTH_CHECK_AUTHORIZATION_REQUEST` / `AUTH_CHECK_AUTHORIZATION_RESPONSE`
- `AUTH_STATUS_REQUEST` / `AUTH_STATUS_RESPONSE`
- `AUTH_SHUTDOWN_REQUEST` / `AUTH_SHUTDOWN_ACK`

#### D. Canal Core ↔ Broker Workers
- `HELLO` / `HELLO_ACK`
- `PING` / `PONG`
- `ORDER_SUBMIT`: Disparo de ordem validada pelo Core para execução no broker.
- `ORDER_ACCEPTED`: Confirmação de recebimento e aceite da corretora.
- `ORDER_REJECTED`: Rejeição imediata por parâmetros, saldo ou mercado.
- `ORDER_STATUS_UNKNOWN`: Falha de resposta ou timeout após envio de ordem.
- `ORDER_STATUS_REQUEST` / `ORDER_STATUS_RESPONSE`: Consulta de status para reconciliação.
- `ORDER_EVENT`: Notificação assíncrona de atualização de contrato (ex: resultado de liquidação `WON`/`LOST`).
- `BROKER_CAPABILITIES_REQUEST` / `BROKER_CAPABILITIES_RESPONSE`
- `MARKET_SYMBOLS_REQUEST` / `MARKET_SYMBOLS_RESPONSE`
- `MARKET_CONTRACTS_REQUEST` / `MARKET_CONTRACTS_RESPONSE`
- `MARKET_TICK_SUBSCRIBE` / `MARKET_TICK_EVENT` / `MARKET_TICK_UNSUBSCRIBE`
- `MARKET_CANDLE_SUBSCRIBE` / `MARKET_CANDLE_EVENT` / `MARKET_CANDLE_UNSUBSCRIBE`

---

## 3. Contratos das Aplicações (`apps/`)

### 3.1 `apps/core` (Trading Core)

O coração do sistema. Gerencia persistência atômica, verificação de risco, reconciliação e supervisão de workers.

#### Exports Públicos Canônicos (`apps/core/__init__.py`)
- **Supervisão e Clientes IPC:**
  - `WorkerSupervisor`: Supervisor dos subprocessos de workers externos.
  - `SocketWorkerClient`: Cliente IPC robusto para envio de ordens com garantias de entrega.
  - `WorkerHealthState`: Enum de saúde do worker (`STARTING`, `READY`, `DEGRADED`, `FAILED`).
  - `DeliveryCertainty`: Nível de certeza de entrega (`CONFIRMED`, `INDETERMINATE`, `FAILED`).
  - `AuthAgentSupervisor`: Supervisor do subprocesso do Auth Agent.
  - `AuthAgentIpcClient`: Cliente de comunicação com o Auth Agent.
- **Coordenação e Execução:**
  - `OrderCoordinator`: Router de ordens entre estratégias, ledger e workers.
  - `PersistedOrder`: Representação em memória da ordem persistida.
  - `CoreRuntime`: Composição de banco, single writer, router e serviços.
  - `HealthGate`: Validador de saúde global e bloqueador de novas entradas.
  - `ReconciliationCoordinator`: Motor de reconciliação de ordens não terminais.
  - `RecoveryCoordinator`: Recuperação de integridade transacional na inicialização.
- **Risco e Configuração:**
  - `DigitRiskConfig`: Estrutura imutável de parametrização de risco da Deriv.
  - `validate_digit_risk_config(config) -> tuple[bool, str]`: Validador estrito de limites.
  - `DERIV_SYNTHETIC_INDEX_ALLOWLIST`: Conjunto de índices autorizados para operação.
- **Pipeline de Estratégias:**
  - `StrategyEntryPipeline`: Fluxo canônico `Sinal → Arbiter → Allocator → Intenção`.
  - `EntryPlan`, `StrategyBatchItem`, `StrategyPipelineResult`.
- **Validação de Configuração:**
  - `ConfigValidator`: Validador de schema e variáveis de ambiente pré-inicialização.
  - `ConfigValidationError`: Exceção agregadora com todos os erros e sugestões.
  - `ValidatedConfig`: Modelo tipado com conversão para minor units (`max_daily_loss_minor_units`).
  - `validate_config(env_file, overrides) -> ValidatedConfig`: Função utilitária canônica.

#### Serviços Internos Críticos de `apps/core`
- **`apps.core.risk.RiskLedger`:**
  - `validate_and_reserve(request: OrderRequest, ...) -> RiskDecision`
  - `release_reservation(reservation_id: str) -> None`
  - `on_order_settled(order_id: str, outcome: str, profit: Money) -> None`
  - `metrics() -> RiskMetrics`
  - `digit_metrics() -> DigitRiskMetrics`
- **`apps.core.iqoption_risk_config.IqOptionRiskConfig`:**
  - Configuração de risco de candles/RSI para IQ Option com limites de Stop Loss, Take Profit e Martingale delimitado.
- **`apps.core.iqoption_auto_trader.IqOptionAutoTrader`:**
  - Motor de execução autônoma multi-ativos (`AUTO`) da IQ Option.
- **`apps.core.deriv_auto_trader.DerivAutoTrader`:**
  - Motor de execução autônoma de estratégias de dígitos da Deriv.

#### Regras de Dependência para `apps/core`
- **PODE IMPORTAR:** `packages.domain`, `packages.protocol`, `packages.persistence`, `packages.portfolio_allocation`, `packages.signal_arbitration`, `packages.strategies`, `packages.strategy_catalog`, `packages.security`, `packages.observability`.
- **NÃO PODE IMPORTAR:** `apps.ui`, `apps.deriv_worker`, `apps.iqoption_worker`, bibliotecas externas de brokers (`deriv_api`, `websocket-client` da IQ).

---

### 3.2 `apps/auth_agent` (Agente de Autenticação e Licença)

Gerencia sessão de usuário local, tokens, identidade do dispositivo e leases criptografadas.

#### Exports Públicos Canônicos (`apps/auth_agent/__init__.py`)
- `AuthAgent`: Classe principal de gerenciamento de sessão e autorização.
- `AuthAgentState`: `SIGNED_OUT`, `CHALLENGE_PENDING`, `AUTHORIZED`, `OFFLINE_AUTHORIZED`, `BLOCKED`, `REAUTH_REQUIRED`.
- `CoreLeaseEntryAuthorizer`: Adaptador de autorização consultado pelo Core.
- `AuthAgentServer`: Servidor IPC do Auth Agent.
- `create_user_scoped_vault`: Factory de vault DPAPI para Windows.

#### Interface de `AuthAgent` (`apps/auth_agent/agent.py`)
```python
class AuthAgent:
    def start_login(self, email: str) -> LoginChallenge: ...
    def complete_login(self, code: OtpCode) -> AuthorizationDecision: ...
    def restore(self) -> AuthorizationDecision: ...
    def renew_silently(self) -> AuthorizationDecision: ...
    def authorization(self, broker: str, strategy_id: str) -> AuthorizationDecision: ...
    def sign_out(self) -> None: ...
    def device_id(self) -> str: ...
    def user_id(self) -> str | None: ...
```

#### Regras de Dependência para `apps/auth_agent`
- **PODE IMPORTAR:** `packages.identity`, `packages.licensing`, `packages.security`, `packages.observability`, `packages.protocol`.
- **NÃO PODE IMPORTAR:** `apps.core`, `apps.ui`, `apps.deriv_worker`, `apps.iqoption_worker`, credenciais ou APIs de corretoras.

---

### 3.3 `apps/deriv_worker` (Worker Deriv)

Subprocesso isolado responsável pela conexão com a API da Deriv via WebSocket.

#### Exports Públicos Canônicos (`apps/deriv_worker/__init__.py`)
- `DerivWorkerServer`: Servidor IPC que atende aos comandos enviados pelo Core.
- `DerivOrderSession`: Sessão de negociação Demo.
- `DerivLiveOrderSession`: Sessão de negociação Real (quando autorizada).
- `DerivReconciliationHandler`: Motor de reconciliação de ordens da Deriv.
- Validadores: `validate_deriv_account`, `validate_deriv_ws_url`, `validate_outbound_deriv_request`.

#### Protocolo e Restrições
- Recebe mensagens `ORDER_SUBMIT`, `ORDER_STATUS_REQUEST`, `MARKET_TICK_SUBSCRIBE` via IPC.
- Transforma respostas da Deriv em `BrokerOrderEvent` canônicos e envia via IPC para o Core.
- **NÃO PODE IMPORTAR:** `apps.core`, `apps.ui`, `packages.persistence`.

---

### 3.4 `apps/iqoption_worker` e `apps/iqoption_connection_worker` (Workers IQ Option)

Subprocessos isolados para integração com a plataforma IQ Option.

#### Exports Públicos Canônicos (`apps/iqoption_worker/__init__.py`)
- `IQOptionWorkerServer`: Servidor IPC para operações financeiras.
- `IQOptionOrderSession`: Gerenciador de execução de ordens binárias/digitais.
- `IQOptionReconciliationHandler`: Reconciliação de ordens abertas.
- `IQOptionErrorCategory`, `IQOptionWorkerError`: Tratamento de categorias de erro da IQ Option.

#### Proteção Anti-Detecção (Stealth) Obrigatória (R-STEALTH-001 a R-STEALTH-003)
- O envio de ordens DEVE aplicar jitter aleatório de 50ms a 250ms.
- Conexões WebSocket DEVEM usar headers padronizados de navegadores modernos (Chrome/Edge no Windows) e User-Agent autêntico.
- **NÃO PODE IMPORTAR:** `apps.core`, `apps.ui`, `packages.persistence`.

---

### 3.5 `apps/launcher` (Supervisor de Processos e Lifecyle)

Entry point de inicialização da árvore de processos, lock de instância única e contenção via Windows Job Object.

#### Exports Públicos Canônicos (`apps/launcher/__init__.py`)
- `ProcessTreeSupervisor`: Gerencia startup ordenado (`AuthAgent` → `Core` → `Workers` → `UI`).
- `LauncherLifecycleState`: Estado do ciclo de vida do launcher.
- `LauncherRestartPolicy`: Política de contenção e reinício limitado de subprocessos.
- `LauncherSnapshot`: Snapshot imutável da árvore de processos.
- `UpdateManager`: Coordenação de atualizações assinadas com staging e rollback.

---

### 3.6 `apps/ui` (Interface Gráfica PySide6)

Interface rica em Qt/PySide6, desacoplada e reativa.

#### Exports Públicos Canônicos (`apps/ui/__init__.py`)
- `TradingLabMainWindow`: Janela principal da aplicação.
- `UiController`: Controlador que executa o loop de polling IPC (500ms) e despacha comandos.
- `DashboardViewModel`: View Model imutável alimentado pelas projeções do Core.
- `CircuitBreaker`, `CircuitOpenError`, `CircuitState`: Resiliência IPC, proteção contra falhas em cascata e bloqueio preventivo.
- `I18nManager`, `t`: Internacionalização (Português, Espanhol, Inglês).
- `get_application_stylesheet`: Estilos escuros/temas visuais QSS.

#### Regras de Comunicação da UI
- A UI comunica-se **exclusivamente via `UiController`** enviando envelopes para o `CoreUiService`.
- A UI nunca toca em classes de conexão de rede externa, nunca acessa SQLite e nunca importa `apps.core.runtime` ou `apps.*_worker`.

---

## 4. Contratos dos Pacotes Reutilizáveis (`packages/`)

### 4.1 `packages/domain` (Modelos Canônicos de Domínio)

Modelos imutáveis (`@dataclass(frozen=True)`), sem dependências de infraestrutura, UI ou banco.

- **Financeiro e Ordens:**
  - `Money(minor_units: int, currency: str)`: Representação canônica de valor.
  - `OrderCommand`: Ordem a ser executada com deadline, IDs de correlação e payload.
  - `OrderRequest`: Solicitação com valor reservado, símbolo e parâmetros.
  - `OrderState`: `CREATED`, `PENDING_RISK`, `SUBMITTED`, `OPEN`, `SETTLED_WON`, `SETTLED_LOST`, `REJECTED`, `UNKNOWN`.
  - `BrokerOrderEvent`: Evento assíncrono emitido por worker de broker.
- **Mercado e Séries:**
  - `MarketTick`: Cotação em tempo real com timestamp de fonte e recebimento.
  - `MarketCandle`: Barra de candle com OHLCV e timestamp fechado.
  - `BrokerAccountBalance`: Saldo auditado com moeda e modalidade (`DEMO`/`REAL`).
  - `BrokerCapabilities`: Lista imutável de operações suportadas pela conexão.

---

### 4.2 `packages/persistence` (Persistência e Armazenamento Crítico)

Persistência transacional atômica em SQLite configurado em modo WAL (`Write-Ahead Logging`).

#### Exports Públicos Canônicos (`packages/persistence/__init__.py`)
- `SingleDatabaseWriter`: **Único objeto autorizado a abrir transações de escrita** no banco financeiro.
- `FinancialUnitOfWork`: Contexto de escrita atômica garantindo:
  1. Criação/transição de intenção da ordem;
  2. Reserva de risco no ledger persistido;
  3. Registro de comando na outbox para envio ao worker.
- `StateReader`: Leituras consistentes (somente leitura) que não bloqueiam o escritor WAL.
- `DatabaseHealth`, `DatabaseHealthState`, `DatabaseFailureReason`: Telemetria de saúde do SQLite.
- Exceções: `AccountBusyError`, `DatabaseWriteError`, `InvalidOrderTransition`, `ReservationReleaseBlocked`.

---

### 4.3 `packages/risk` e `packages/portfolio_allocation` (Gestão de Risco e Alocação)

- `PortfolioAllocator`: Distribui capital entre diferentes contas e estratégias.
- `BoundedMartingaleAllocator`: Implementação estrita de Martingale delimitado:
  - Exige teto de etapas (`max_steps`);
  - Exige teto financeiro de stake (`max_stake`);
  - Interrompe a sequência imediatamente se ultrapassar o Stop Loss diário consolidado.
- `BoundedMartingaleConfig`, `BoundedMartingaleState`, `BoundedMartingaleProjection`.

---

### 4.4 `packages/signal_arbitration` (Arbitragem de Sinais)

- `SignalArbiter`: Processa sinais gerados por estratégias concorrentes antes do envio ao risco.
  - **Cancelamento de Opostos:** Sinais simultâneos contrários (ex: CALL vs PUT, OVER vs UNDER) anulam a operação.
  - **Deduplicação de Idênticos:** Múltiplos sinais na mesma direção e ativo não multiplicam a stake.
  - **Validação Temporal:** Sinais com prazo expirado são descartados.

---

### 4.5 `packages/strategies` e `packages/strategy_catalog` (Estratégias e Catálogo)

- `StrategyImplementation`: Protocolo que toda estratégia implementa:
  - `evaluate(context: RuntimeContext) -> StrategyEvaluation`
  - `warmup(ticks/candles) -> WarmupCheckpoint`
- Estratégias Deriv de Dígitos:
  - `TailProbabilityEdgeStrategy`: Exploração de cauda estatística.
  - `SelectiveDiffersEdgeStrategy`: Diferencial seletivo de dígitos.
  - `ParityRegimeEdgeStrategy`: Regime de paridade par/ímpar.
- Estratégia IQ Option:
  - `IQOptionRsiDemoStrategy`: Bounded Edge sobre RSI(14) em tempo real.
- `StrategyCatalog` & `StrategyManifest`: Registro de metadados, compatibilidade de ativos e status de validação (`CANDIDATE`, `APPROVED`, `DEPRECATED`).

---

### 4.6 `packages/security` (Criptografia, Vault e Integridade)

- `WindowsUserScopedVault`: Armazenamento seguro de credenciais protegido pelo escopo DPAPI do usuário do Windows (`CryptProtectData`/`CryptUnprotectData`).
- `SecretValue`: Invólucro de memória segura que impede vazamento acidental de tokens/senhas em `repr()` ou logs.
- `ReleaseIntegrityVerifier`: Validador de manifesto de integridade SHA-256 e Ed25519 dos arquivos executáveis.
- `SecretScanner`: Scanner preventivo de código para evitar que chaves privadas ou tokens fiquem gravados no projeto.

---

### 4.7 `packages/observability` (Auditoria, Eventos e Diagnóstico)

- `EventSink`, `PersistentJsonlEventSink`: Registro em arquivo JSONL append-only para auditoria.
- `DiagnosticBundleBuilder`: Criação de arquivo `.zip` com logs redigidos e sanitizados para suporte técnico (sem vazar senhas ou tokens).

---

## 5. Matriz de Permissões de Importação

A tabela a seguir estabelece as permissões e proibições de imports diretos de código no projeto:

| Camada / Módulo | Pode Importar | NUNCA Pode Importar |
|---|---|---|
| **`apps/ui/`** | `packages.protocol.ui_messages`, `packages.domain`, tipos utilitários locais de UI | `apps.core`, `apps.*_worker`, `packages.persistence`, bibliotecas de broker |
| **`apps/core/`** | `packages.*`, `apps.core.*` | `apps.ui`, `apps.deriv_worker`, `apps.iqoption_worker` |
| **`apps/deriv_worker/`** | `packages.domain`, `packages.protocol`, `packages.brokers.deriv`, `packages.observability` | `apps.core`, `apps.ui`, `packages.persistence` |
| **`apps/iqoption_worker/`** | `packages.domain`, `packages.protocol`, `packages.brokers.iqoption`, `packages.observability` | `apps.core`, `apps.ui`, `packages.persistence` |
| **`apps/auth_agent/`** | `packages.identity`, `packages.licensing`, `packages.security`, `packages.protocol` | `apps.core`, `apps.ui`, `apps.*_worker`, dados financeiros |
| **`packages/domain/`** | Apenas biblioteca padrão do Python (`typing`, `dataclasses`, `datetime`, `decimal`) | Qualquer outro pacote do repositório (`packages.*` ou `apps.*`) |
| **`packages/protocol/`** | `packages.domain`, biblioteca padrão | `apps.*`, `packages.persistence` |
| **`packages/persistence/`**| `packages.domain`, `packages.observability`, sqlite3 | `apps.*`, `packages.protocol` |
| **`packages/strategies/`** | `packages.domain`, `packages.strategy_catalog` | `apps.*`, `packages.persistence`, APIs de rede |

---

## 6. Regras de Governança para Agentes de IA

1. **Alteração de Contrato:** Antes de modificar qualquer assinatura pública, classe de envelope ou campo de modelo de domínio, atualize **obrigatoriamente este documento**.
2. **Prevenção de Dependências Circulares:** Sempre verifique a matriz de permissões antes de adicionar um `import`.
3. **Compatibilidade Retroativa:** Mantenha compatibilidade de mensagens e schemas JSON sempre que novos campos forem adicionados (use campos opcionais com valor padrão).
4. **Validação Obrigatória:** Após qualquer modificação em código de contrato, execute a suite canônica de verificação:
   ```bash
   python -m compileall apps packages
   python -m pytest tests/unit -q
   python -m ruff check .
   ```
5. **Comportamento Falhar Fechado:** Diante de dúvida ou inconsistência em contrato financeiro, bloqueie a criação de ordens e registre o evento no `EventSink`.
