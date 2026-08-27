# Referência de componentes — v1.9.11

Este documento mapeia os módulos Python de produção. Arquivos `__init__.py` expõem APIs públicas do
pacote e arquivos `__main__.py` fornecem entry points executáveis.

## 1. apps/auth_agent

| Módulo | Responsabilidade |
|---|---|
| `agent.py` | fluxo local de login, dispositivo, sessão e lease |
| `core_gate.py` | autorização reduzida consultada pelo Core; combina lease e sessão Deriv |
| `fake_service.py` | provedor simulado de identidade/licenciamento |
| `runner.py` | startup do subprocesso por documento JSON em stdin |
| `server.py` | servidor IPC autenticado do Auth Agent |
| `vault_factory.py` | seleção do vault Windows ou simulação explícita |

## 2. apps/launcher

| Módulo | Responsabilidade |
|---|---|
| `cli.py` | argumentos, perfil, verificação de manifesto e loop principal |
| `core_client.py` | cliente IPC do lifecycle do Core |
| `deriv_login.py` | diálogo Qt de token, contas e confirmação Real |
| `instance.py` | lock de instância única por perfil |
| `models.py` | estados/snapshots imutáveis do Launcher |
| `process_controller.py` | spawn do Core/UI, tokens efêmeros, Job Object e escalada de término |
| `supervisor.py` | ordem de startup, health polling, restart limitado e shutdown |
| `updater_service.py` | orquestra verificação, aplicação e rollback de atualização |
| `windows_job.py` | contenção da árvore de processos no Windows |

## 3. apps/core

| Módulo | Responsabilidade |
|---|---|
| `auth_client.py` | cliente IPC do Auth Agent |
| `auth_supervisor.py` | ciclo de vida/restart do Auth Agent |
| `broker_events.py` | aplicação idempotente e pump de eventos de ordem |
| `broker_shadow_session.py` | sessão shadow compartilhada por broker e séries |
| `broker_shadow_soak.py` | runner e relatórios de soak temporal |
| `candle_pipeline.py` | ponte de candle fechado para pipeline de estratégia |
| `coordinator.py` | router de broker, serialização por conta, outbox e ordem |
| `deriv_auto_trader.py` | loop de execução automática Demo das estratégias de dígitos |
| `deriv_telemetry.py` | saldo/relógio/ticks, radar multiativo e projeções estatísticas |
| `diagnostic_service.py` | coleta contexto redigido para diagnóstico |
| `digit_risk_config.py` | configuração, allowlists e validação de risco de dígitos |
| `digit_risk_store.py` | persistência JSON atômica da configuração |
| `health.py` | Health Gate global e escopado |
| `instance.py` | lock exclusivo do Core no perfil |
| `lifecycle_server.py` | servidor IPC de lifecycle para o Launcher |
| `lifecycle_service.py` | composição do Core, conexão/reconexão Deriv e shutdown |
| `read_only_worker_supervisor.py` | supervisor genérico para workers Deriv públicos/autenticados |
| `reconciliation.py` | consulta e aplicação de evidência de ordens não terminais |
| `recovery.py` | recuperação local no startup |
| `risk.py` | Risk Ledger global e especializado, cooldown e martingale |
| `runner.py` | entry point do Core e parsing do startup por stdin |
| `runtime.py` | banco, writer, worker simulado, pumps, router e serviços principais |
| `shadow_host.py` | host bounded com fairness, circuit breaker e budgets |
| `shadow_runtime.py` | runtime shadow supervisionado e geração de conexão |
| `soak_cli.py` | entry point do soak |
| `soak_cli_runtime.py` | argumentos, matriz, relatórios e exit codes do soak |
| `soak_profiles.py` | perfis e agendas determinísticas de falhas |
| `strategy_pipeline.py` | Runtime → Arbiter → Allocator → intenção |
| `ui_service.py` | construção de snapshots e servidor de comandos da UI |
| `worker_client.py` | cliente IPC financeiro/market data e certeza de entrega |
| `worker_supervisor.py` | health, heartbeat, restart policy e circuit breaker |

## 4. apps/deriv_login_helper

| Módulo | Responsabilidade |
|---|---|
| `__main__.py` | abre diálogo isolado, grava no vault e retorna apenas `saved/cancelled` |

## 5. apps/deriv_worker

| Módulo | Responsabilidade |
|---|---|
| `demo_session.py` | REST Options, descoberta de conta, OTP, saldo e sessão autenticada |
| `fake_transport.py` | transporte determinístico para testes locais |
| `mapper.py` | payload Deriv → modelos canônicos imutáveis |
| `order_session.py` | proposal, buy, eventos de contrato e sessão financeira Demo |
| `public_session.py` | mercado público, catálogo, histórico e subscriptions |
| `reconciliation.py` | resolução de ordem via dados Deriv |
| `request_allowlist.py` | operações permitidas/proibidas por modo |
| `schema.py` | erros e modelos internos do worker |
| `server.py` | servidor IPC, handshake, capabilities e despacho de mensagens |
| `subscriptions.py` | estado, gap, stale, duplicidade e backpressure de subscriptions |
| `tick_stream.py` | stream de ticks e frequência dos últimos dígitos |
| `validators.py` | endpoint, conta, payload e restrições de modo |
| `websocket_client.py` | transporte websocket real, reader thread e filas limitadas |

## 6. apps/iqoption_worker

| Módulo | Responsabilidade |
|---|---|
| `order_session.py` | sessão/harness de ordem IQ para laboratório |
| `reconciliation.py` | consulta de status simulada/contratual |
| `schema.py` | modelos e erros do worker IQ |
| `server.py` | servidor IPC de teste |

Não existe sessão externa de usuário operacional na v1.9.11.

## 7. apps/simulated_worker

| Módulo | Responsabilidade |
|---|---|
| `broker_store.py` | banco externo sintético de ordens/eventos |
| `scenarios.py` | aceite, rejeição, timeout, crash e outros cenários |
| `server.py` | servidor IPC do worker simulado |
| `worker.py` | comportamento financeiro sintético |

## 8. apps/ui

| Módulo | Responsabilidade |
|---|---|
| `app.py` | janela principal, navegação, ações e atualização de projeções |
| `controller.py` | polling de 500 ms e comandos da UI |
| `formatting.py` | formatação monetária a partir de minor units |
| `i18n.py` | catálogo espanhol/inglês e troca de idioma |
| `ipc_client.py` | handshake, reconexão e mensagens UI/Core |
| `runner.py` | startup por stdin e loop Qt/headless |
| `theme.py` | paleta e stylesheet QSS |
| `view_model.py` | view model imutável da dashboard |

### apps/ui/components

| Módulo | Responsabilidade |
|---|---|
| `asset_radar_panel.py` | ranking multiativo e abstenção/candidato |
| `broker_card.py` | conexão, saldo, modo e relógio por broker |
| `deriv_strategy_summary.py` | cards de resultado e risco sem scroll |
| `deriv_workspace.py` | hub Deriv, biblioteca, tabs e status do bot |
| `digit_config_panel.py` | editor validado de risco/martingale |
| `digit_frequency_widget.py` | frequência visual dos dígitos 0–9 |
| `health_pill.py` | blockers do Health Gate |
| `order_table.py` | tabela de ordens e estados |
| `results_dashboard.py` | ganhos, perdas, taxa e P&L confirmado |
| `risk_gauge.py` | exposição global e limite |
| `safe_stop_button.py` | controle dedicado de Safe Stop |
| `synthetic_strategy_panel.py` | parâmetros e mercado live da estratégia ativa |
| `workspaces.py` | workspaces genéricos de broker e configurações |

## 9. packages/domain

| Módulo | Responsabilidade |
|---|---|
| `canonical.py` | serialização canônica para hashes/assinaturas |
| `market.py` | ticks, candles, símbolos, contratos, saldo e relógio |
| `models.py` | dinheiro, brokers, ordens, eventos e estados canônicos |

Regras essenciais: dinheiro em minor units, moeda explícita, timestamps UTC e modelos imutáveis.

## 10. packages/protocol

| Módulo | Responsabilidade |
|---|---|
| `auth_messages.py` | mensagens do Auth Agent |
| `codec.py` | codificação/decodificação estrita |
| `envelope.py` | envelope versionado, IDs, papéis e deadlines |
| `errors.py` | códigos estáveis de protocolo |
| `framing.py` | prefixo de tamanho e limite do frame |
| `lifecycle_messages.py` | controle Launcher/Core |
| `messages.py` | worker, ordem, evento e market data |
| `transport.py` | socket framed |
| `ui_messages.py` | projeções e comandos da UI |
| `version.py` | versão do protocolo |

## 11. packages/persistence

| Módulo | Responsabilidade |
|---|---|
| `backup.py` | backup SQLite consistente e verificado |
| `candle_repository.py` | candles idempotentes e conflitos |
| `database.py` | conexões, WAL, integridade e marker |
| `health.py` | estados/reasons de saúde do banco |
| `journal_repository.py` | journal de decisões append-only |
| `migrations.py` | schema financeiro e checksums |
| `reader.py` | consultas query-only para projeção/recovery |
| `replay_repository.py` | resultados de replay |
| `strategy_commit_repository.py` | commit atômico candle + decisão + checkpoint |
| `strategy_data.py` | schema separado de dados/estratégias |
| `validation_repository.py` | evidências de validação de estratégia |
| `warmup_repository.py` | checkpoints de warm-up |
| `writer.py` | único writer financeiro, unit of work e transições |

## 12. packages/market_data

| Módulo | Responsabilidade |
|---|---|
| `ingress.py` | validação/aceite de market data |
| `models.py` | identidade e estado de séries |
| `source.py` | portas de origem de dados |
| `store.py` | armazenamento local de séries |
| `tick_ring_buffer.py` | janela de ticks, frequência e transições |
| `time.py` | tempo da fonte e detecção de suspensão |

## 13. packages/market_pipeline

| Módulo | Responsabilidade |
|---|---|
| `clock.py` | clock monotônico/fonte |
| `coordinator.py` | backfill, continuidade e geração |
| `dispatcher.py` | entrega somente quando health permite |
| `health.py` | estado de market data por série |
| `live_router.py` | demultiplexação de um stream compartilhado |
| `live.py` | subscription live e reconexão |
| `models.py` | modelos do pipeline |
| `planner.py` | páginas com overlap |
| `scheduler.py` | agenda bounded com backoff/fairness |

## 14. packages/strategies

| Módulo | Responsabilidade |
|---|---|
| `checkpoint.py` | estado determinístico e hash do checkpoint |
| `deriv_digits.py` | três estratégias de dígitos e radar multiativo |
| `models.py` | contexto, sinal, evidência e decisões genéricas |
| `runtime.py` | execução determinística por candle fechado |

## 15. packages/strategy_catalog

| Módulo | Responsabilidade |
|---|---|
| `catalog.py` | registro, compatibilidade, status e entitlement |
| `metrics.py` | métricas históricas de estratégia |
| `models.py` | manifesto, versão e ciclo de vida |
| `validation.py` | relatórios de backtest/walk-forward/practice |
| `walk_forward.py` | janelas temporais não sobrepostas |

## 16. packages/signal_arbitration

| Módulo | Responsabilidade |
|---|---|
| `arbiter.py` | cancela opostos, deduplica iguais e valida expiração |
| `models.py` | decisão e reason codes de arbitragem |

## 17. packages/portfolio_allocation

| Módulo | Responsabilidade |
|---|---|
| `allocator.py` | orçamentos por estratégia, conta e global |
| `martingale.py` | progressão pura e limitada da stake |
| `models.py` | solicitações/decisões de alocação |

## 18. packages/replay

| Módulo | Responsabilidade |
|---|---|
| `clock.py` | clock virtual determinístico |
| `engine.py` | sessão e execução de replay |
| `models.py` | pedido, resultado e status de replay |
| `persistent_journal.py` | integração do replay com journal persistente |

## 19. packages/audit

| Módulo | Responsabilidade |
|---|---|
| `decision_journal.py` | registro estruturado de decisões |
| `journal.py` | hash chain/journal append-only |
| `models.py` | eventos e provas de auditoria |

## 20. packages/identity e packages/licensing

| Área | Responsabilidade |
|---|---|
| `identity/device.py` | ID aleatório e chave de dispositivo |
| `identity/models.py` | modelos de usuário/dispositivo |
| `licensing/lease.py` | assinatura, verificação e autorização da lease |
| `licensing/models.py` | entitlement, validade e compatibilidade |

Essa infraestrutura opera com o provedor simulado atual.

## 21. packages/security

| Módulo | Responsabilidade |
|---|---|
| `dpapi.py` | CryptProtectData/CryptUnprotectData CurrentUser |
| `integrity.py` | manifesto SHA-256 e verificação da distribuição |
| `process_environment.py` | remoção de variáveis de credenciais antes do spawn |
| `secret_scanner.py` | scanner bounded e redigido |
| `secrets.py` | `SecretValue` e contrato de vault |
| `updater.py` | manifesto Ed25519, staging, backup e rollback |
| `vault.py` | interfaces de vault |
| `windows_vault.py` | envelope DPAPI, DACL por SID e escrita atômica |

## 22. packages/observability

| Módulo | Responsabilidade |
|---|---|
| `diagnostic.py` | construção do ZIP de diagnóstico |
| `events.py` | eventos operacionais allowlisted |
| `retention.py` | JSON atômico e retenção limitada |

## 23. packages/brokers

### Deriv

| Módulo | Responsabilidade |
|---|---|
| `candle_adapter.py` | candle Deriv → domínio |
| `candle_pump.py` | histórico fechado via IPC |
| `contracts.py` | portas e capacidades Deriv |
| `credentials.py` | conta/tipo/token no vault DPAPI |
| `models.py` | modelos específicos reduzidos |
| `product_config.py` | App ID público do produto |

### IQ Option

| Módulo | Responsabilidade |
|---|---|
| `contracts.py` | interfaces do adapter IQ |
| `fake_transport.py` | transporte de laboratório |
| `session.py` | sessão de teste |
| `validators.py` | prática permitida e Real bloqueada |

## 24. build_scripts

| Arquivo | Responsabilidade |
|---|---|
| `TradingLab.spec` | configuração PyInstaller onedir/windowed |
| `compile_trading_lab.py` | compila, escaneia, gera manifesto e faz health check |
| `build_windows_onedir.py` | staging onedir alternativo |
| `version_info.txt` | metadados do executável Windows |
| `TradingLab_Setup.iss` | instalador Inno Setup |
| `PortableLauncher.cs` | invólucro portátil, payload e instância única |

## 25. tests

| Diretório | Finalidade |
|---|---|
| `unit` | funções, modelos, limites e componentes isolados |
| `contract` | IPC, worker e compatibilidade de fronteira |
| `integration` | Core + SQLite + subprocessos + lifecycle |
| `replay` | equivalência e determinismo |
| `chaos` | kill, crash, timeout e recuperação |
| `security` | varredura e controles de segurança |
| `external` | smokes Deriv explicitamente opt-in |
| `helpers` | processos/fixtures auxiliares de teste |

Na revisão desta documentação, o coletor encontrou 613 testes.
