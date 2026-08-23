# Rastreabilidade — Requisitos, Código e Testes

## 1. Objetivo

Esta matriz aponta evidências atuais sem substituir o PRD. “Implementado” significa provado na Fase
1 local/practice, não liberado comercialmente. O código/teste é fonte factual; o `WORKLOG` registra
a evolução.

## 2. Arquitetura e autoridade

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-002, R-ARCH-002, R-DB-001 | Launcher/Core instance guards, `CoreRuntime`, Single Writer | launcher + storage integration | implementado local |
| R-ARCH-001/007 | Launcher Job Object + subprocessos/supervisores isolados | process-tree + IPC integration | parcial; UI/IQ ausentes |
| R-ARCH-008 | protocol envelope/framing/version + lifecycle HMAC | IPC unit/contract | implementado v1 local |
| AG-INV-004 | readers query-only e writer no Core | storage resilience | implementado |
| FR-072/073, R-ORD-008 | safe stop → drain → workers → Auth → Core | launcher unit/integration | implementado local |
| NFR-004/020 | health polling, kill isolation e restart bounded não financeiro | process-tree integration | implementado local Windows |

## 3. Ordens, risco e persistência

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-045, FR-052 | coordinator + writer transaction | persistence/dispatch, concurrency | simulado |
| FR-053 | serialização/claim de outbox | persistence/dispatch | simulado |
| FR-054, AG-INV-002 | delivery certainty/`UNKNOWN` | crash/reconciliation | implementado simulado |
| FR-046, AG-INV-003 | reserva ativa em unknown | storage/reconciliation | implementado |
| FR-055 | broker event processor/settlement transaction | order lifecycle | implementado simulado |
| FR-056 | event queue/pump bounded | events/backpressure | implementado simulado |
| R-DB-002 | minor units/Decimal | domain/unit/integration | implementado |

Arquivos centrais: `apps/core/coordinator.py`, `apps/core/risk.py`,
`packages/persistence/writer.py`, `packages/persistence/migrations.py`.

## 4. Recovery e reconciliação

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-060 | supervisor/backoff/circuit | worker contract/integration | simulado/read-only |
| FR-061 | startup recovery + candidates | recovery/integration | implementado |
| FR-062 | reconciliation coordinator/evidence | reconciliation protocol | simulado |
| FR-063 | outcome manual review | reconciliation tests | contrato local |
| FR-064 | monotonic suspension/gap recovery | market pipeline | implementado local |
| R-STATE-003/004 | event idempotency/no terminal regression | order lifecycle | implementado |

## 5. Identidade e licenciamento

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-090/091 | fake email+OTP, stable user ID | auth/licensing unit | simulado |
| FR-092/093 | PKCE, short/rotating tokens | auth/licensing unit | simulado |
| FR-094 | random device + Ed25519 proof | auth/licensing unit | simulado |
| FR-095, R-AUTH-005 | DPAPI CurrentUser + vault persistente + DACL por SID | DPAPI unit + Windows vault integration | implementado local Windows |
| FR-096/097 | signed practice lease/gate | auth + lease entry integration | simulado |
| FR-099, R-AUTH-009/010 | Auth Agent subprocess + IPC autenticado + decisão reduzida | auth IPC contract + subprocess kill/restart | implementado local Windows |
| R-AUTH-009 | expiry/revocation não interrompe ordem | auth/strategy integrations | implementado simulado |

Arquivos: `apps/auth_agent/`, `packages/identity/`, `packages/licensing/`, `packages/security/`.

## 6. Strategy Platform

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-030, R-CAT-001/002 | manifest/catalog/validation | strategy platform unit | implementado local |
| FR-031, R-CAT-005 | runtime context key | unit/replay | implementado |
| FR-032, R-STR-006 | same runtime replay/shadow | replay/shadow integration | implementado sintético |
| FR-034 | evidence/signals/journal | unit/replay | implementado sintético |
| R-CAT-006 | Runtime→Arbiter→Allocator→Risk | strategy pipeline integration | implementado |
| R-CAT-007/008 | opposite cancel/same no stake sum | unit/integration | implementado |
| R-CAT-009 | strategy/account/global budgets | allocator tests | implementado local |
| R-CAT-010 | suspension blocks only new entries | strategy integration | implementado simulado |
| R-CAT-012 | packaged local code only | catalog validation | implementado |

Nenhuma estratégia comercial foi liberada; validation evidence dos testes é sintética.

## 7. Market data, replay e shadow

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-023, R-DATA-005 | CandleIngress + Market Health | unit/integration | implementado |
| R-DATA-001/007 | provenance + strict adapters | contract/unit | implementado |
| R-DATA-002 | monotonic scheduler/clock | market scheduler tests | implementado |
| R-DATA-006 | `strategy_data.db` separado | persistence tests | implementado |
| R-STR-003 | closed candle only | candle ingress tests | implementado |
| replay determinístico | journal/checkpoint/replay engine | replay + chaos | implementado sintético |
| reconnect/backfill | scheduler/runtime generation | integration/chaos | implementado fake IPC |
| shared broker stream | router/session | shared stream integration | implementado Deriv fake |
| bounded soak | host/soak/temporal/matrix | unit/integration | implementado local |
| CLI/atomicidade/retenção | `soak_cli` + observability retention | unit + subprocess integration | implementado local |
| perfis/fault schedule | `soak_profiles` + CLI profiled report | unit + subprocess integration | implementado sintético |

Arquivos: `packages/market_data/`, `packages/market_pipeline/`, `packages/replay/`,
`apps/core/shadow_*`, `apps/core/broker_shadow_*`.

## 8. Deriv e IQ Option

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| Deriv market data read-only | `apps/deriv_worker/` | unit/contract/integration | fake padrão; público opt-in |
| FR-010 demo account | demo session architecture | unit only | parcial read-only, sem conta usada |
| trading operation disabled | request allowlist/capability | unit/contract | implementado |
| real account forbidden | websocket/demo guards | unit | implementado |
| FR-011 IQ practice | nenhum worker | nenhum | não implementado |
| FR-012 isolamento | topologia/process boundaries | simulated/Deriv tests | parcial; IQ futuro |

## 9. Observabilidade e segurança

| Requisito | Implementação | Testes | Estado |
|---|---|---|---|
| FR-020/071 | state/reason snapshots | unit/integration | projeções locais; UI ausente |
| FR-080 | audit/event/journal | persistence/replay | parcial local |
| FR-083 | diagnostic package | contrato documental | não implementado |
| R-SEC-001/008, R-TEST-007 | `SecretScanner` bounded + redaction | security + soak integration | implementado local |
| R-DB-007, NFR-023 | SQLite Backup API + restore isolado | backup restore drill | implementado em harness |
| R-SEC-003 | JSON IPC, no pickle | protocol tests | implementado |
| R-SEC-004 | signed update/rollback | docs only | não implementado |
| R-SEC-007 | remote telemetry opt-in | nenhuma telemetria | futuro |

## 10. UI, instalação e release

| Requisito | Estado |
|---|---|
| FR-001 instalação | não implementado; CLI local executável existe |
| FR-002 instância por perfil | locks Launcher + Core implementados; redirecionamento UI pendente |
| FR-070–075 UI | não implementado |
| FR-081–084 consulta/exportação | não implementado, exceto evidência interna |
| FR-004 atualização | não implementado |
| R-REL-001–004 | arquitetura/documentação, sem pipeline executável |

## 11. Conta real

FR-015 e os critérios do PRD não estão implementados. Guards Deriv rejeitam superfícies/contas
reais, e a lease da Fase 0 proíbe real. Isso é bloqueio, não evidência de prontidão para liberar.

## 12. Suíte de evidência

| Diretório | Evidência |
|---|---|
| `tests/unit` | modelos, validação, estados e limites |
| `tests/contract` | IPC/workers/adapters |
| `tests/integration` | Core/SQLite/subprocessos/pipelines |
| `tests/replay` | determinismo e restore |
| `tests/chaos` | kill real em boundaries de commit |
| `tests/external` | Deriv pública read-only opt-in |

Resultados de execuções materiais são registrados em `WORKLOG.md`, nunca inferidos desta matriz.

## 13. Lacunas de rastreabilidade

- IDs de requisitos ainda não estão anotados em todos os testes;
- não há gerador automatizado da matriz;
- não há coverage report configurado;
- UI/release/diagnóstico não possuem testes;
- IQ e conta real permanecem sem implementação;
- requisitos legais/regionais continuam abertos.
