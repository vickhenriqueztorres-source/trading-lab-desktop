# Documentação completa — Trading Lab Desktop v1.9.11

Este diretório é o ponto de entrada da documentação da implementação atual. Os documentos abaixo
separam uso do aplicativo, arquitetura, estratégias, risco, desenvolvimento, operação e release.

> A fonte de verdade final continua sendo o código e seus testes. Documentos históricos do projeto
> podem descrever fases anteriores. Quando houver divergência, consulte primeiro este índice e os
> documentos marcados como **estado atual v1.9.11**.

## Leitura recomendada

### Para quem usa o aplicativo

1. [Visão geral e estado atual](PROJECT_OVERVIEW.md)
2. [Manual do usuário](USER_GUIDE.md)
3. [Estratégias Deriv e gestão de risco](DERIV_STRATEGIES_AND_RISK.md)
4. [Solução de problemas](TROUBLESHOOTING.md)
5. [IQ Option: correção da confirmação do relógio](IQ_CLOCK_SYNC_FIX_20260909.md)
6. [IQ Option: resiliência de relógio, WebSocket e retomada](IQOPTION_CONNECTION_RESILIENCE_20260910.md)

### Para desenvolvimento e manutenção

1. [Guia universal de desenvolvimento em qualquer IDE](UNIVERSAL_IDE_DEVELOPMENT_GUIDE.md)
2. [Guia completo de estratégias e execução IQ Option](IQOPTION_FULL_IMPLEMENTATION_AND_STRATEGY_GUIDE.md)
3. [Arquitetura atual](CURRENT_ARCHITECTURE.md)
4. [Contratos de interface públicos](INTERFACE_CONTRACTS.md)
5. [Referência de componentes](COMPONENT_REFERENCE.md)
6. [Guia de desenvolvimento, testes e build](DEVELOPMENT_BUILD_AND_TEST.md)
7. [Persistência e recuperação](PERSISTENCE_AND_RECOVERY.md)
8. [Protocolo IPC v1](IPC_PROTOCOL_V1.md)
9. [Segurança](../SECURITY.md)
10. [Plano do catálogo incremental e Supabase](PLANO_CATALOGO_INCREMENTAL_SUPABASE_PROMPTS.md)
11. [CAT-00 — baseline e auditoria do catálogo](CAT00_BASELINE_AND_CATALOG_AUDIT.md)
12. [ADR — semântica de execução e bootstrap](ADR_EXECUTION_SEMANTICS_AND_BOOTSTRAP.md)
13. [CAT-01 — inventário e orçamento do Supabase](CAT01_SUPABASE_INVENTORY_AND_CAPACITY_BUDGET.md)
14. [CAT-02 — contrato público de receita e capacidades](CAT02_PUBLIC_RECIPE_CONTRACT.md)
15. [CAT-03 — dataset, identidade e payout as-of](CAT03_DATASET_IDENTITY_AND_ASOF.md)
16. [CAT-13 — publicação recuperável do manifesto](CAT13_RECOVERABLE_MANIFEST_PUBLICATION.md)
17. [CAT-14 — consumo do manifesto no runtime](CAT14_MANIFEST_RUNTIME_CONSUMPTION.md)
18. [CAT-15 — outcomes, privacidade e budgets](CAT15_OUTCOMES_PRIVACY_AND_BUDGETS.md)
19. [CAT-16 — UI verdadeira e telemetria local](CAT16_TRUE_UI_AND_LOCAL_TELEMETRY.md)
20. [CAT-17 — arquivo frio verificável e restaurável](CAT17_VERIFIED_COLD_ARCHIVE.md)
21. [Martingale delimitado G1/G2 da IQ Option](IQOPTION_BOUNDED_MARTINGALE_20260910.md)
22. [Correção de resultado, saldo e martingale por candle da IQ Option](IQOPTION_RESULT_BALANCE_CANDLE_MARTINGALE_CORRECTION_PLAN_20260910.md)
23. [Plano pós-auditoria da madrugada: UNKNOWN, relógio, backoff e prioridade G1/G2](IQOPTION_OVERNIGHT_RELIABILITY_CORRECTION_PLAN_20260911.md)

### Para produto e governança

- [PRD](../PRD_Trading_Desktop_Deriv_IQOption.md)
- [Arquitetura histórica e planejada](../Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md)
- [Contratos de interface](../INTERFACE_CONTRACTS.md)
- [Regras obrigatórias](../RULES.md)
- [Guardrails de IA](../AIGUARD.md)
- [Plano de testes](../TEST_PLAN.md)
- [Rastreabilidade](TRACEABILITY.md)
- [Histórico de trabalho](../WORKLOG.md)

## Estado funcional resumido

| Área | Estado na v1.9.11 |
|---|---|
| Aplicativo Windows | Implementado, UI PySide6 e executável portátil |
| Instância única | Implementada no lançador portátil e por perfil no Launcher/Core |
| Deriv sem login | Dados públicos/fake como modo inicial seguro |
| Deriv API Token/PAT | Implementado com seleção explícita da conta |
| Deriv Demo | Conexão, saldo, ticks, análise e ordens automatizadas implementados |
| Deriv Real | Conexão e monitoramento somente leitura; ordens bloqueadas |
| IQ Option | Conexão, saldo, radar multi-ativos, auto-seleção, estratégias e ordens implementados |
| Estratégias Deriv | Três estratégias de dígitos implementadas |
| Estratégias IQ Option | RSI 14 Bounded Edge multi-ativos e suporte a novas estratégias implementados |
| Seleção automática de ativo | Implementada para Deriv (R_10 a R_100) e IQ Option (Radar com todos os pares OTC/Forex) |
| Martingale | Opcional, delimitado, desativado por padrão |
| Persistência financeira | SQLite/WAL com writer único, outbox e reconciliação |
| Diagnóstico | Terminal UI ao vivo e ZIP local, ambos redigidos, limitados e sem segredos |
| Atualização | Componentes de verificação/rollback existem; distribuição comercial não configurada |
| Assinatura Authenticode | Não implementada |

## Documentos legados ou especializados

- [CAT-19 — Baseline local do catálogo incremental](CAT19_CATALOG_BENCHMARK_BASELINE.md)
- [CAT-18 — Retenção, quota e limpeza fechada](CAT18_RETENTION_AND_QUOTA.md)

- [Recuperação de rejeições IQ por escopo — Causa 5](IQOPTION_SCOPED_FAILURE_RECOVERY.md)

- [Validação do roteamento IQ pelo manifesto — Causa 2](IQOPTION_MANIFEST_ROUTING_VALIDATION.md)
- [Contrato de warmup verificado](WARMUP_CONTRACT_VALIDATION.md)

Os arquivos abaixo continuam úteis, mas alguns descrevem a evolução do projeto e não substituem a
visão consolidada da v1.9.11:

- [Deriv Worker](DERIV_WORKER.md)
- [IQ Option Worker](IQOPTION_WORKER.md)
- [Pipeline de market data](MARKET_DATA_PIPELINE.md)
- [Candle fechado e replay](CLOSED_CANDLE_REPLAY.md)
- [Observabilidade](OBSERVABILITY.md)
- [Terminal de logs operacionais na UI](UI_LOG_TERMINAL_20260909.md)
- [Códigos de erro e saúde](ERROR_AND_HEALTH_CODES.md)
- [Runbook operacional histórico](OPERATIONS_RUNBOOK.md)
- [Processo de release](RELEASE_PROCESS.md)
- [Arquitetura de informação da UI](UI_INFORMATION_ARCHITECTURE.md)

## Convenção de precisão

- [Portões de execução do manifesto: plano, implementação e validação](MANIFEST_EXECUTION_GATES_VALIDATION.md)

- **Implementado** significa que existe código executável e testes correspondentes.
- **Somente leitura** significa que a sessão pode consultar dados, mas não recebe capacidade de
  submissão de ordens.
- **Simulado** significa que o comportamento existe para testes locais, não que uma integração
  externa esteja pronta para o usuário.
- **Planejado** significa que aparece em PRD/arquitetura, mas não está disponível no executável.
- Nenhuma estratégia ou métrica apresentada constitui garantia de lucro.
