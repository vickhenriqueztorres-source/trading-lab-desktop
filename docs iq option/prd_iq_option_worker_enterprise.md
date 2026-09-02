# PRD — IQ Option Worker Enterprise

**Versão:** 1.0
**Status:** Draft para aprovação técnica
**Data:** 31/08/2026
**Produto:** Trading Lab Desktop — IQ Option Worker
**Ambiente inicial:** Conta demo/practice, sem operação real habilitada

> Este PRD define requisitos de produto, arquitetura, operação, segurança e validação para um worker resiliente de integração com a IQ Option. Não promete disponibilidade de 100%, execução exatamente uma vez ou recuperação automática quando a API remota não fornece confirmação idempotente.

## 1. Resumo executivo

O IQ Option Worker será um serviço isolado responsável por conectar-se à API utilizada pelo projeto, consumir dados de mercado, receber intenções de ordem do orquestrador e executar operações somente quando o sistema estiver consistente, autenticado, eleito como líder e liberado pelos circuit breakers.

O produto prioriza **fail-safe**, consistência verificável e rastreabilidade. Em qualquer situação em que o resultado remoto seja desconhecido, o worker deverá interromper novas entradas da conta/ativo afetado, reconciliar com a plataforma e somente depois voltar ao estado operacional.

A arquitetura consolida os problemas identificados no material de análise: SPOF, race conditions, vazamento de memória em streams, retry excessivo, circuit breaker simplificado, credenciais inseguras, ausência de SLOs, deploy/rollback, migrações, testes de caos, auditoria, capacity planning e disaster recovery. [file:1]

## 2. Objetivos

### 2.1 Objetivos principais

- Isolar a integração com a IQ Option em processo próprio.
- Permitir recuperação automática de falhas sem duplicar ordens.
- Garantir um único escritor ativo por conta e ambiente.
- Persistir a máquina de estados das ordens.
- Tratar timeout de envio como resultado `UNKNOWN`.
- Reconciliar o estado local com o estado remoto após reconexão ou restart.
- Impedir que estratégias chamem diretamente a API do broker.
- Reduzir risco de memory leak em streams e tasks.
- Disponibilizar logs estruturados, métricas, tracing e auditoria.
- Permitir deploy progressivo e rollback controlado.
- Validar a operação por testes unitários, integração, carga, caos e restore.

### 2.2 Não objetivos

- Garantir lucro, performance financeira ou recomendação de investimento.
- Garantir execução exatamente uma vez quando a API remota não oferecer idempotência ou consulta confiável da ordem.
- Operar conta real na primeira versão.
- Criar uma nova estratégia de trading.
- Fazer scaling horizontal de escritores ativos para a mesma conta.
- Ocultar divergências de estado para manter o sistema aparentemente disponível.

## 3. Usuários e stakeholders

| Perfil | Necessidade |
|---|---|
| Operador | Ver estado, conexão, ordens, alertas e motivos de bloqueio |
| Desenvolvedor | Testar adapter, eventos, estados e falhas determinísticas |
| SRE/DevOps | Implantar, monitorar, recuperar e fazer rollback |
| Segurança | Auditar credenciais, acessos, configurações e eventos |
| Produto | Acompanhar SLOs, riscos operacionais e prontidão |

## 4. Escopo funcional

### 4.1 Fluxo de ordem

```text
Signal
  → validação
  → deduplicação
  → policy/risk gate
  → verificação de líder
  → reserva persistida
  → SUBMITTING
  → resposta do broker
       ├─ aceitação → ACCEPTED
       ├─ rejeição explícita → REJECTED_REMOTE
       └─ timeout/queda → UNKNOWN
                                  → bloqueio
                                  → reconciliação
                                  → confirmação ou MANUAL_REVIEW
```

### 4.2 Estados do worker

```text
STARTING
CONNECTING
AUTHENTICATING
SYNCING
READY
DEGRADED
READ_ONLY
RECONCILING
HALTED
SHUTTING_DOWN
```

O worker só poderá aceitar novas entradas quando estiver em `READY`, possuir lease válido, fencing token atual, conexão autenticada, reconciliação concluída e breakers relevantes fechados.

### 4.3 Estados da ordem

```text
CREATED → ADMITTED → RESERVED → SUBMITTING
                              ├→ ACCEPTED → OPEN → SETTLED
                              ├→ REJECTED_REMOTE
                              └→ UNKNOWN → RECONCILING
                                              ├→ ACCEPTED
                                              ├→ REJECTED_REMOTE
                                              └→ MANUAL_REVIEW
```

## 5. Requisitos funcionais

### RF-01 — Gateway único

Todas as estratégias deverão enviar sinais ou intenções para o `Command Gateway`. Nenhuma estratégia poderá importar ou instanciar diretamente o cliente da IQ Option.

**Critério de aceitação:** teste automatizado falha caso uma estratégia tenha acesso direto ao adapter do broker.

### RF-02 — Deduplicação determinística

O sistema deverá criar `dedupe_key` a partir de:

```text
account_id + strategy_id + asset + candle_open_time + direction + duration + strategy_signal_version
```

A chave deverá possuir índice único persistido.

**Critério de aceitação:** 100 requisições do mesmo sinal resultam em uma única intenção admitida.

### RF-03 — Single writer

Por conta e ambiente, somente o worker que possuir lease válido e fencing token atual poderá submeter ordens.

**Critério de aceitação:** comandos de worker com fencing token antigo serão recusados antes da chamada ao broker.

### RF-04 — Lease de liderança

O lease deverá ser adquirido e renovado atomically no State Store. A perda do lease deverá mover o worker para `READ_ONLY` imediatamente.

Configuração inicial:

```yaml
lease_ttl_seconds: 45
renew_interval_seconds: 15
min_time_between_leader_changes_seconds: 30
fencing_required: true
```

### RF-05 — Reserva de exposição

Antes do envio, a ordem deverá criar uma reserva persistida. A reserva não será considerada saldo remoto; será exposição pendente local.

```text
estimated_available = remote_balance - confirmed_open_exposure - pending_reservations
```

Ordens em `UNKNOWN` manterão a reserva até reconciliação.

### RF-06 — Resultado desconhecido

Timeout, queda de conexão ou resposta inválida após o envio deverão gerar `UNKNOWN`. O sistema não poderá reenviar automaticamente a mesma ordem sem reconciliação.

### RF-07 — Reconciliação

Após reconnect, restart, timeout de envio, perda de evento ou divergência, o worker deverá consultar:

- saldo;
- ordens abertas;
- ordens recentes;
- resultados liquidados;
- posições/exposições disponíveis;
- janela desde o último evento confirmado, com margem configurável.

### RF-08 — Adapter de broker

O adapter deverá expor uma interface estável e mapear erros externos para erros de domínio:

```text
NetworkTransientError
SessionExpiredError
RateLimitedError
OrderRejectedError
OrderUnknownError
UnsupportedCapabilityError
BrokerProtocolError
```

O adapter deverá publicar um `CapabilityMap`, incluindo, quando verificável:

```python
REMOTE_ORDER_LOOKUP
CLIENT_IDEMPOTENCY
OPEN_ORDERS_QUERY
SETTLED_ORDERS_QUERY
BALANCE_QUERY
STREAM_RECONNECT
```

### RF-09 — Conexão e retry

O `ConnectionManager` será o único proprietário da reconexão. O cliente interno da biblioteca não poderá executar retry infinito concorrente.

A recuperação só poderá marcar a conexão como operacional depois de:

1. autenticação;
2. subscrição restabelecida;
3. snapshot remoto obtido;
4. reconciliação finalizada.

### RF-10 — Streams

Deverá existir uma única stream por combinação `asset + timeframe` por processo. O registry deverá possuir:

- ID de subscription;
- unsubscribe explícito;
- shutdown da stream quando não houver subscribers;
- limite de subscribers;
- cancelamento aguardado de tasks;
- métricas de lifecycle;
- contexto `async with` para cleanup garantido.

`WeakRef` será mecanismo auxiliar, não substituto do lifecycle explícito.

### RF-11 — Backpressure

Dados de mercado poderão usar política `latest-only` quando apropriado. Eventos de ordem deverão ser persistidos ou rejeitados explicitamente, nunca descartados silenciosamente.

### RF-12 — Circuit breakers

Haverá breakers independentes para:

- conexão;
- market data;
- consulta de conta;
- envio de ordens;
- autenticação.

Falhas de autenticação bloquearão envio. Timeout de ordem criará `UNKNOWN`. Rate limit reduzirá a taxa e respeitará indicação remota quando disponível.

### RF-13 — Credenciais

Credenciais deverão ser obtidas por provider seguro:

- keyring do sistema para desktop;
- Vault/KMS em servidor;
- `.env` somente em desenvolvimento.

Senhas não serão persistidas em snapshots, eventos, logs ou metadados de ordens.

### RF-14 — Auditoria

O sistema deverá registrar acesso a credenciais, mudanças de configuração, eleições de líder, submissões, rejeições, reconciliações, deploys e rollbacks sem armazenar segredos.

### RF-15 — Observabilidade

O worker deverá expor liveness, readiness e trading readiness separadamente, além de logs JSON, métricas e correlation ID.

### RF-16 — Supervisor externo

O processo deverá ser supervisionado por systemd, Windows Service, Docker ou outro supervisor externo. O supervisor deverá reiniciar o processo com backoff, detectar loop de crash e preservar logs.

## 6. Arquitetura técnica

```text
┌──────────────┐
│ UI/ViewModel │
└──────┬───────┘
       ▼
┌──────────────┐     ┌──────────────────┐
│ Command      │────▶│ Order Coordinator│
│ Gateway      │     │ single writer    │
└──────┬───────┘     └────────┬─────────┘
       ▼                      ▼
┌──────────────┐     ┌──────────────────┐
│ Strategy     │     │ Policy/Risk Gate │
│ Runtime      │     └────────┬─────────┘
└──────┬───────┘              ▼
       ▼               ┌───────────────┐
┌──────────────┐       │ Broker Port   │
│ Market Data  │       └───────┬───────┘
│ Fan-out      │               ▼
└──────┬───────┘       ┌───────────────┐
       │                │ IQ Adapter   │
       │                └───────┬───────┘
       │                        ▼
       │                ┌───────────────┐
       │                │ IQ client/API │
       │                └───────────────┘
       ▼
┌─────────────────────────────────────┐
│ State Store: events, leases, orders │
│ reservations, snapshots, audit      │
└─────────────────────────────────────┘
```

### 6.1 Topologia inicial

```text
Processo A: Desktop UI + Gateway + Strategies
Processo B: IQ Option Worker
Processo C: Supervisor externo
Persistência: SQLite local na fase desktop
```

### 6.2 Topologia HA

```text
Host A: Worker A
Host B: Worker B standby
Shared Store: PostgreSQL ou Redis com lease atômico
Supervisor: externo em cada host
Observabilidade: serviço separado e não obrigatório para execução segura
```

SQLite não será tratado como mecanismo de HA entre hosts. Em deployment distribuído, PostgreSQL será o padrão para eventos, ordens e auditoria; Redis poderá ser usado para lease e sinais efêmeros, desde que a semântica seja explicitamente validada.

## 7. Persistência e schema

### 7.1 Entidades mínimas

```text
accounts
workers
worker_leases
orders
order_events
order_reservations
positions
remote_snapshots
reconciliation_runs
idempotency_keys
audit_events
schema_versions
deployment_events
```

### 7.2 Garantias do banco

- índice único para `dedupe_key`;
- índice único para `(account_id, fencing_token, command_sequence)`;
- transação para admissão + reserva;
- eventos append-only;
- snapshots versionados;
- auditoria com retenção definida;
- integridade verificada antes de retomar trading.

### 7.3 Migração expand-and-contract

1. `EXPAND`: adicionar colunas/tabelas compatíveis com a versão anterior;
2. deploy de código que lê e escreve formato novo e, temporariamente, mantém compatibilidade;
3. `MIGRATE`: preencher dados em batches;
4. validar contagem, checksums e leitura dos dois formatos;
5. `CONTRACT`: remover campos somente após janela de segurança e confirmação de que nenhum processo antigo está ativo.

Toda migração deverá ter:

- versão;
- owner;
- checksum;
- timeout;
- plano de rollback ou declaração de irreversibilidade;
- teste em cópia de produção;
- verificação pós-migração.

## 8. SLOs e SLIs

Os valores abaixo são metas iniciais para o modo demo e deverão ser recalibrados com medições reais da API.

```yaml
slo:
  availability_of_trading_ready:
    target: 99.5%
    window: 30d
  order_gateway_ack_p95_ms: 500
  order_gateway_ack_p99_ms: 1000
  reconciliation_p95_seconds: 30
  reconciliation_p99_seconds: 60
  duplicate_internal_submission_rate: 0
  unknown_orders_unresolved_over_15m: 0
  audit_event_write_success: 99.99%
```

A disponibilidade de `trading_ready` não será calculada como se o broker estivesse sempre disponível. Indisponibilidade causada pela própria plataforma deverá ser classificada separadamente de indisponibilidade do worker.

SLIs:

```text
worker_liveness
worker_readiness
worker_trading_ready
order_state_transition_latency
broker_request_latency
reconciliation_duration
unknown_order_count
leader_lease_remaining
queue_depth
memory_rss
subscription_count
```

Alertas de burn rate serão configurados em janelas curta e longa. Nenhuma alteração de threshold poderá ser feita sem registro de configuração e revisão.

## 9. Segurança

### 9.1 Controles

- menor privilégio para processos;
- credenciais fora do código;
- redaction centralizado;
- rotação e revogação de emergência;
- MFA quando suportado pela plataforma;
- bloqueio de tentativas de autenticação;
- criptografia em trânsito e em repouso;
- auditoria de acesso;
- dependências fixadas e verificadas;
- secret scanning no CI;
- proibição de senha em dumps, logs e snapshots.

### 9.2 Redaction obrigatória

Campos sempre removidos ou mascarados:

```text
password
api_token
cookie
authorization
session
secret
master_key
```

## 10. Deploy e rollback

### 10.1 Estratégia

A primeira versão deverá usar rolling deploy controlado em demo. Blue-green/canary será usado quando houver dois ambientes operacionais independentes.

Fluxo:

1. lint, type check e testes;
2. build reprodutível;
3. scan de segurança;
4. migração `EXPAND`;
5. iniciar nova versão em `READ_ONLY`;
6. executar smoke test;
7. adquirir lease somente após readiness;
8. canary com tráfego de sinais não financeiros;
9. habilitar execução demo;
10. monitorar SLOs;
11. promover gradualmente;
12. concluir ou fazer rollback.

### 10.2 Gatilhos de rollback

- falha de health check;
- aumento de erro acima do limite;
- aumento de latência P99;
- divergência de reconciliação;
- ordem `UNKNOWN` sem tratamento correto;
- violação de fencing;
- vazamento de segredo;
- crescimento anormal de memória;
- crash loop.

Rollback de aplicação não deverá apagar eventos nem executar automaticamente `CONTRACT`. Migrações destrutivas exigirão procedimento separado e aprovação explícita.

## 11. Testes

### 11.1 Pirâmide

- unitários: domínio, estados, dedupe, breakers;
- contrato: Broker Port e adapters;
- integração: State Store, lease, eventos;
- end-to-end: worker + mock broker;
- smoke: conta demo;
- carga: throughput e latência;
- caos: falhas controladas;
- DR: restore e reconciliação.

### 11.2 Cenários obrigatórios

- 100 sinais simultâneos para o mesmo ativo;
- mesmo sinal em múltiplas estratégias;
- dois workers competindo por lease;
- perda de lease durante envio;
- timeout antes, durante e depois do envio;
- resposta duplicada ou fora de ordem;
- banco indisponível durante reconciliação;
- crash abrupto do worker;
- memória crescendo após milhares de subscribe/unsubscribe;
- callback lento ou com exceção;
- credencial inválida;
- rate limit;
- snapshot corrompido;
- ordem remota sem evento local;
- evento local sem ordem remota.

### 11.3 Critérios de passagem

- zero duplicação interna em testes de dedupe;
- zero envio pelo worker sem lease;
- nenhum retry automático de `UNKNOWN` sem reconciliação;
- todas as tasks canceladas no shutdown;
- restore validado por checksum e contagem;
- divergência bloqueia trading;
- secrets ausentes em logs e artefatos;
- SLOs demonstrados em ambiente de teste.

## 12. Capacity planning

Métricas mínimas:

```text
CPU
RSS memory
queue depth
broker latency
database latency
database connections
orders/sec
signals/sec
active streams
strategy processing time
reconciliation backlog
```

Limites iniciais:

```yaml
capacity:
  cpu_warning_percent: 70
  cpu_critical_percent: 85
  memory_warning_percent: 75
  memory_critical_percent: 90
  queue_warning_percent: 60
  queue_critical_percent: 80
  queue_backpressure_percent: 90
  p99_latency_warning_ms: 800
```

Scaling horizontal não deverá criar múltiplos escritores para a mesma conta. O scaling deve ocorrer em:

- validação de sinais;
- market-data fan-out;
- estratégias isoladas;
- reconciliações particionadas quando seguro.

O envio da mesma conta permanecerá single-writer.

## 13. Disaster recovery

### 13.1 Metas iniciais

```yaml
rpo: 1h
rto: 4h
```

Essas metas não incluem o tempo de indisponibilidade ou limitações da plataforma de broker.

### 13.2 Backups

- banco: backup completo a cada 6 horas;
- eventos: backup contínuo ou incremental conforme backend;
- configurações: diário;
- retenção mínima: 14 dias para eventos e 30 dias para configuração;
- criptografia;
- verificação automática;
- teste de restore semanal em ambiente isolado.

### 13.3 Recuperação

```text
desastre
  → parar novos envios
  → provisionar infraestrutura
  → restaurar banco/eventos
  → validar integridade
  → iniciar worker em READ_ONLY
  → autenticar broker
  → buscar snapshot remoto
  → reconciliar
  → resolver divergências
  → habilitar trading somente após aprovação automática
```

Nunca habilitar envio apenas porque o banco restaurou. O broker remoto é necessário para confirmação de ordens e exposições.

## 14. Auditoria

Cada evento deverá conter:

```text
timestamp
actor_id
actor_type
resource_type
resource_id
action
success
correlation_id
worker_id
fencing_token
metadata_redacted
previous_hash
event_hash
signature
```

O log deverá ser append-only, ter controle de acesso separado e passar por verificação periódica de integridade. A auditoria não deverá depender exclusivamente da memória do worker.

Eventos de segurança prioritários:

- login sucesso/falha;
- acesso/alteração/revogação de credencial;
- mudança de configuração;
- eleição/perda de líder;
- tentativa com fencing token inválido;
- abertura/fechamento de breaker;
- entrada em `UNKNOWN`;
- divergência de reconciliação;
- deploy e rollback.

## 15. Operação e runbooks

Runbooks obrigatórios:

1. worker não inicia;
2. autenticação falha;
3. lease perdido;
4. ordem `UNKNOWN`;
5. divergência local/remota;
6. banco indisponível;
7. fila saturada;
8. memória acima do limite;
9. rate limit;
10. rollback;
11. restore;
12. revogação emergencial de credencial.

Cada runbook deverá informar:

- sintomas;
- diagnóstico;
- comandos permitidos;
- condição de bloqueio;
- procedimento de recuperação;
- critério de encerramento;
- evidências a anexar.

## 16. Fases de entrega

### Fase 0 — Fundação

- Broker Port;
- mock broker;
- máquina de estados;
- State Store local;
- dedupe;
- redaction;
- logs estruturados;
- conta demo;
- trading desabilitado por padrão.

### Fase 1 — Worker seguro

- processo isolado;
- Connection Manager;
- backoff;
- circuit breakers;
- reconciliação;
- fila de ordens;
- single writer local.

### Fase 2 — Alta disponibilidade

- PostgreSQL/Redis;
- lease e fencing token;
- standby em processo separado;
- supervisor externo;
- failover testado.

### Fase 3 — Enterprise operacional

- SLO dashboards;
- canary/blue-green;
- migrações automatizadas;
- auditoria imutável;
- load/chaos tests;
- backups e restore automatizados;
- runbooks e treinamento operacional.

### Fase 4 — Prontidão controlada

- validação prolongada em demo;
- revisão de segurança;
- revisão de dependências;
- aprovação operacional;
- habilitação manual e gradual de qualquer ambiente real.

## 17. Critérios de go/no-go

### Go

- reconciliação pós-restart validada;
- fencing token validado em concorrência;
- timeout não gera retry duplicado;
- restore concluído dentro do RTO;
- secrets não aparecem em logs;
- supervisor recupera crash;
- SLOs medidos por janela suficiente;
- alertas testados;
- runbooks aprovados;
- operação demo estável.

### No-go

- qualquer ordem ambígua é reenviada automaticamente;
- múltiplos workers podem enviar sem fencing;
- State Store possui placeholders críticos;
- `except Exception: return False` oculta resultado;
- SQLite é usado como HA entre hosts;
- migração destrutiva sem backup e rollback;
- ausência de reconciliação remota;
- credenciais em configuração, logs ou snapshots;
- observabilidade inexistente;
- testes somente unitários;
- API não oficial sem capability map ou validação de contrato.

## 18. Riscos e mitigação

| Risco | Impacto | Mitigação |
|---|---|---|
| API não oficial muda protocolo | Alto | Adapter isolado, capability map, contrato e smoke tests |
| Timeout após envio | Muito alto | `UNKNOWN`, bloqueio e reconciliação |
| Split-brain | Muito alto | Lease, fencing token e single writer |
| Banco corrompido | Alto | Eventos append-only, checksums, backups e restore |
| Memory leak | Médio/alto | Lifecycle explícito, limites, métricas e restart controlado |
| Rate limit | Médio | Rate limiter, jitter, backoff e breaker |
| Credencial vazada | Muito alto | Keyring/Vault, redaction, auditoria e revogação |
| Falha de observabilidade | Médio | Execução segura sem depender de métricas externas |
| Crash loop | Alto | Supervisor externo, backoff e alerta crítico |
| Dependência incompatível | Alto | Lockfile, testes de contrato e rollout progressivo |

## 19. Definição de pronto

Uma entrega será considerada pronta quando:

- os requisitos funcionais e não funcionais estiverem implementados;
- os testes obrigatórios passarem;
- não houver placeholders em caminhos críticos;
- as migrações estiverem versionadas;
- os dashboards e alertas estiverem ativos;
- o procedimento de rollback tiver sido exercitado;
- o restore tiver sido comprovado;
- a reconciliação remota tiver cobertura de sucesso e divergência;
- o sistema iniciar em modo seguro;
- a documentação operacional estiver revisada.

## 20. Decisão arquitetural final

O produto deverá adotar:

```text
single writer por conta
+ lease distribuído com fencing token
+ event store persistente
+ máquina de estados explícita
+ UNKNOWN como estado de primeira classe
+ reconciliação obrigatória
+ adapter isolado
+ um único dono de retry
+ lifecycle explícito de streams
+ breakers por domínio
+ supervisor externo
+ observabilidade com SLOs
+ deploy reversível
+ backups testados
```

A arquitetura é considerada **profissional/enterprise-ready no desenho**, mas só poderá ser classificada como **enterprise em produção** depois que os requisitos acima forem implementados, testados sob falha e operados com evidências. O documento de análise fornecido contém uma boa base de resiliência, mas também apresenta placeholders, garantias excessivas e exemplos que ainda precisam ser substituídos por implementação verificável. [file:1]
