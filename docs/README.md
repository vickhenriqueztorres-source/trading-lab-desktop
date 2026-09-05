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

### Para desenvolvimento e manutenção

1. [Guia universal de desenvolvimento em qualquer IDE](UNIVERSAL_IDE_DEVELOPMENT_GUIDE.md)
2. [Guia completo de estratégias e execução IQ Option](IQOPTION_FULL_IMPLEMENTATION_AND_STRATEGY_GUIDE.md)
3. [Arquitetura atual](CURRENT_ARCHITECTURE.md)
4. [Referência de componentes](COMPONENT_REFERENCE.md)
5. [Guia de desenvolvimento, testes e build](DEVELOPMENT_BUILD_AND_TEST.md)
6. [Persistência e recuperação](PERSISTENCE_AND_RECOVERY.md)
7. [Protocolo IPC v1](IPC_PROTOCOL_V1.md)
8. [Segurança](../SECURITY.md)

### Para produto e governança

- [PRD](../PRD_Trading_Desktop_Deriv_IQOption.md)
- [Arquitetura histórica e planejada](../Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md)
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
| Diagnóstico | ZIP local redigido, limitado e escaneado contra segredos |
| Atualização | Componentes de verificação/rollback existem; distribuição comercial não configurada |
| Assinatura Authenticode | Não implementada |

## Documentos legados ou especializados

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
