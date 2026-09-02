# AGENTS — Instruções para Agentes do Repositório

**Baseline obrigatória:** v1.9.11
**Atualizado em:** 2026-09-01

## 1. Contexto

Este repositório contém o Trading Lab Desktop, um aplicativo Windows local que executa estratégias
na Deriv e na IQ Option em modos Demo/Practice e Real, sob rigoroso controle de risco pelo Trading Core,
persistência transacional SQLite e camada stealth de proteção anti-detecção de robôs.

O sistema compartilha o Trading Core, mas mantém as integrações das corretoras em workers separados. O projeto trata falha parcial como comportamento normal e deve bloquear novas entradas quando não puder comprovar segurança operacional.

## 2. Leia antes de agir

Para qualquer tarefa relevante, leia:

1. `AIGUARD.md`;
2. `RULES.md`;
3. `PRD_Trading_Desktop_Deriv_IQOption.md`;
4. `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`;
5. `AUTHENTICATION_AND_LICENSING.md` quando a tarefa tocar usuário, licença ou broker login;
6. `STRATEGY_PLATFORM.md` quando tocar estratégias, catálogo ou sinais;
7. `SECURITY.md` e `TEST_PLAN.md` quando aplicáveis;
8. `WORKLOG.md`;
9. `docs/README.md` para localizar a documentação operacional atual;
10. arquivos diretamente relacionados à tarefa;
11. `AGENTS.md` mais específico existente no subdiretório afetado.

Não comece codificação financeira apenas pelo título da tarefa.

## 3. Mapa arquitetural

```text
Launcher / Supervisor
├── UI (Visão Geral, Deriv, IQ Option, Atividade, Configurações)
├── Auth Agent
│   ├── PKCE / sessão
│   ├── Token Vault (DPAPI CurrentUser)
│   ├── Device Identity
│   └── Offline Lease
├── Trading Core
│   ├── Command Bus & Event Bus
│   ├── State Machines
│   ├── Deriv Auto Trader (Digit Edge, Over/Under, Differs, Even/Odd)
│   ├── IqOption Auto Trader (Multi-Asset Radar, RSI 14, Bollinger, Moving Averages)
│   ├── Stealth Anti-Detection Engine (Jitter, Browser Headers, Realistic Pacing)
│   ├── Signal Arbiter & Portfolio Allocator
│   ├── Risk Ledger (Stop Loss, Take Profit, Max Stake, Max Losses)
│   ├── Durable Outbox & Single Database Writer
│   ├── Recovery Coordinator & Health Gate
│   └── Worker Supervisor (IPC v1 loopback)
├── Deriv Worker
└── IQ Option Worker

Plano de controle remoto mínimo
├── Customer Identity
├── Device Registry
├── Subscription / Entitlements
├── Signed License Lease
├── Strategy Catalog
└── Compatibility Manifest
```

O Core é o único dono do estado financeiro. Workers traduzem protocolos e executam chamadas de rede com proteção stealth. UI apresenta projeções. Estratégias geram sinais. O Auth Agent administra sessão/dispositivo/lease sem receber credenciais de corretora.

Na implementação atual **v1.9.11**, Deriv e IQ Option suportam operações completas em modo **Practice (Demo)** e **Real**:
- **Deriv:** Estratégias de dígitos (`Tail Probability Edge`, `Selective Differs Edge`, `Parity Regime Edge`), warm-up de 500 ticks, seletor de volatilidade e Bounded Martingale.
- **IQ Option:** Motor `IqOptionAutoTrader` com **Radar Multi-Ativos (`AUTO`)** cobrindo todos os 15 pares OTC e Forex, cálculo de RSI(14) em tempo real, disparo instantâneo no primeiro sinal e evasão anti-detecção (jitter aleatório de 50ms-250ms e headers autênticos de navegador).

O Launcher executa a árvore sob `profile.lock`, guard independente do Core, mutex de instância do portátil e Windows Job Object. O canal lifecycle solicita Safe Stop, drain e shutdown sem abrir o banco. Kill do Core/Launcher encerra descendentes e o próximo startup executa recovery e reconciliação.

## 4. Estrutura do repositório

```text
apps/
├── launcher/
├── ui/
├── core/
├── deriv_worker/
└── iqoption_worker/
packages/
├── domain/
├── protocol/
├── identity/
├── licensing/
├── risk/
├── strategies/
├── strategy_catalog/
├── signal_arbitration/
├── persistence/
├── replay/
├── security/
└── observability/
tests/
├── unit/
├── contract/
├── integration/
├── replay/
├── chaos/
└── end_to_end/
```

Consulte `docs/COMPONENT_REFERENCE.md` para o mapa executável por arquivo.

## 5. Fluxo de trabalho

### Antes da alteração

1. Inspecione o estado do repositório e preserve mudanças do usuário.
2. Identifique o requisito/ID do PRD atendido.
3. Classifique o risco da mudança.
4. Defina processo dono do estado.
5. Se tocar identidade/licença, separe claramente identidade DualTrade de credenciais de broker.
6. Se tocar estratégia, identifique manifesto, versão, status, entitlement e ponto de arbitragem.
7. Liste cenários de timeout, crash, restart, expiração/revogação e duplicidade.
8. Verifique se a mudança afeta os dois brokers ou apenas um worker.

### Durante a alteração

1. Faça mudanças pequenas e coesas.
2. Mantenha domínio livre de dependências de corretora/UI.
3. Modele estados explicitamente; não use booleans vagos como `is_done` para ordem.
4. Preserve IDs de correlação em todos os limites.
5. Adicione testes junto com a implementação.
6. Nunca esconda erro crítico para manter sessão em `READY`.
7. Não exponha `user_id`, tokens, device key ou lease como credenciais manuais de UX.
8. Preserve a ordem Strategy Runtime → Signal Arbiter → Portfolio Allocator → Risk Ledger.
9. Expiração/revogação de licença bloqueia novas entradas, mas não interrompe ordens abertas.
10. Aplique a camada stealth anti-detecção em integrações externas da IQ Option.

### Depois da alteração

1. Execute testes e verificações disponíveis.
2. Revise logs e fixtures para segredos.
3. Verifique comportamento de falha fechado.
4. Atualize documentação afetada.
5. Acrescente entrada no `WORKLOG.md`.
6. Relate arquivos alterados, validação e riscos residuais.

## 6. Comandos do projeto

A baseline v1.9.11 usa os seguintes comandos canônicos, configurados no `pyproject.toml`:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

## 7. Convenções de implementação

- Python com type hints em fronteiras públicas.
- `dataclass(frozen=True)` ou modelo imutável para mensagens/eventos.
- `Decimal` ou minor units para valores financeiros.
- UTC para persistência e monotonic clock para duração.
- enums explícitos para estados e motivos de bloqueio.
- códigos de erro estáveis para UI e suporte.
- payload externo validado antes de virar modelo de domínio.
- dependência da IQ confinada ao worker IQ e packages brokers.
- dependência de UI confinada ao app UI.
- SQL e migrações concentrados no pacote de persistência.
- funções de estratégia sem efeitos externos.
- cliente desktop sem `client_secret` confiável ou segredo mestre embutido.
- Device ID aleatório com chave própria; não usar hardware fingerprint como autenticação principal.
- material sensível de sessão/dispositivo protegido no escopo do usuário do Windows via DPAPI.

## 8. Critério de conclusão do agente

Uma tarefa só está concluída quando:

- atende ao pedido e aos requisitos relacionados;
- preserva `AIGUARD.md` e `RULES.md`;
- possui validação proporcional ao risco;
- não deixa estados financeiros implícitos;
- não expõe segredos;
- preserva a camada stealth anti-detecção da IQ Option;
- preserva acompanhamento de ordens abertas diante de expiração/revogação;
- atualiza o worklog quando material;
- documenta o que não foi possível validar;
- fornece um próximo passo claro quando houver trabalho restante.
