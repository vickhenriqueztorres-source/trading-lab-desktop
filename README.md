# Trading Lab Desktop (DualTrade Engine)

Plataforma desktop Windows para automação resiliente de estratégias em Deriv Demo. Conta Deriv
Real pode ser conectada explicitamente em modo somente leitura; IQ Option permanece uma integração
de laboratório, sem sessão externa operacional no aplicativo.

> **Estado atual:** conexão Deriv por API Token/PAT e seleção interna de conta Demo ou Real. As três
> estratégias de dígitos podem executar ordens somente em Demo, após o usuário pressionar
> **Ligar Bot**, sob Risk Ledger, uma ordem por vez e reconciliação durável. Queda da sessão
> autenticada bloqueia novas entradas, obtém novo OTP em worker substituto e só reabre após nova
> evidência saudável. Real continua disponível apenas para monitoramento read-only.

## Segurança primeiro

Operar dinheiro real envolve risco de perda. Nesta versão:

- conta Real nunca é selecionada automaticamente;
- o tipo de conta é comprovado pela API e pelo endpoint OTP correspondente;
- Real exige confirmação explícita e permanece tecnicamente read-only nesta versão;
- IQ Option ainda não possui integração executável;
- estratégias sintéticas são usadas para provar determinismo, não rentabilidade;
- timeout potencialmente aceito vira `UNKNOWN` e nunca recebe retry automático;
- o bot inicia desligado e execução automática é limitada à conta Demo;
- reconexão de mercado nunca reenvia a ordem financeira que ficou ambígua;
- `UNKNOWN` continua como exposição até reconciliação comprovada;
- o Core é a única autoridade financeira local.

## Conectar à Deriv com API Token

Abra o `TradingLab.exe` normalmente. Depois, entre na aba **Deriv**, abra **Configuração**, clique
em **Conectar conta Deriv**:

1. cole um API Token/PAT com permissão `trade`;
2. clique em **Validar token e carregar contas**;
3. escolha a conta Demo ou Real retornada pela Deriv;
4. se escolher Real, confirme o risco e digite `REAL`.

O App ID do produto já está incorporado internamente. O token é armazenado criptografado pelo DPAPI
do Windows e só é aberto dentro do helper de credenciais/Deriv Worker. A API oficial lista as contas
Options ativas, e a conta escolhida precisa corresponder exatamente ao WebSocket Demo ou Real
entregue por OTP. **Nunca cole token em chat, log ou arquivo de texto.** Se escolher **Continuar sem
Deriv**, a tela principal permanece aberta com dados públicos read-only. Nas próximas vezes, o botão
permite reutilizar a credencial já protegida sem digitar novamente.

Antes de qualquer mudança relevante, leia nesta ordem:

1. [AIGUARD.md](AIGUARD.md)
2. [RULES.md](RULES.md)
3. [AGENTS.md](AGENTS.md)
4. [PRD](PRD_Trading_Desktop_Deriv_IQOption.md)
5. [Arquitetura resiliente](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md)
6. documentos específicos da área afetada
7. [WORKLOG.md](WORKLOG.md)

## O que está implementado

- Single Core Instance por perfil.
- `state.db` com Single Database Writer, WAL, integridade, migrações e backup consistente.
- transação atômica de `TradeIntent` + `RiskReservation` + Outbox antes do dispatch simulado;
- máquinas de estado, outbox durável, exposição conservadora e reconciliação por evidência;
- IPC v1 em TCP loopback, JSON framed, limite de 64 KiB e handshake versionado;
- worker financeiro simulado preservado como padrão e Deriv Worker isolado;
- login Deriv token-only, seleção Demo/Real, cofre DPAPI e OTP; a capacidade financeira permanece
  restrita à Demo e o auto trader executa somente a estratégia explicitamente selecionada;
- Auth Agent, PKCE, device identity e signed lease simulados, com vault Windows DPAPI CurrentUser
  persistente, subprocesso isolado, IPC autenticado e fallback de simulação explícito;
- Launcher executável com lock por perfil, Windows Job Object, health snapshots, restart bounded de
  componentes não financeiros e safe shutdown autenticado do Core;
- Strategy Catalog, Runtime, Signal Arbiter, Portfolio Allocator e Risk Ledger local;
- três monitores determinísticos de dígitos Deriv: `Tail Probability Edge` para Over/Under,
  `Selective Differs Edge` para Digit Differs e `Parity Regime Edge` para Even/Odd;
- Bounded Martingale opcional e desativado por padrão para as três estratégias, calculado pelo
  Portfolio Allocator/Core com multiplicador, etapas, teto absoluto de stake, projeção da perda
  máxima da sequência e validação prévia pelo Risk Ledger/Stop Loss diário;
- candle fechado, `strategy_data.db`, journal append-only, checkpoint e replay determinístico;
- scheduler/backfill monotônico, Market Health Gate, shadow contínuo e recovery por geração;
- sessão Deriv compartilhada por várias séries, host bounded e soak temporal comparativo;
- CLI de soak explicitamente opt-in com perfis/fault presets, JSON atômico e retenção FIFO bounded;
- scanner local bounded de segredos aplicado ao relatório antes da publicação;
- restore drill de backup em perfil temporário isolado, com integridade e preservação do original.

O estado detalhado e a próxima fatia ficam em [ROADMAP.md](ROADMAP.md) e
[WORKLOG.md](WORKLOG.md).

## O que não está implementado

- operação financeira externa em IQ Option;
- integração executável IQ Option practice;
- teste automatizado ou externo com dinheiro real;
- provedor de identidade remoto e validação multiusuário/instalador do vault Windows;
- estratégia comercial `RELEASED`;
- assinatura Authenticode, publicação comercial e autoatualização remota;
- telemetria remota;
- daemon/serviço agendado de soak.

## Arquitetura resumida

```text
UI PySide6 ──projeções/comandos──> Trading Core
                                   ├── state.db (estado financeiro)
                                   ├── Risk Ledger / Health Gate
                                   ├── estratégias de dígitos + radar
                                   ├── auto trader Demo
                                   └── recovery / reconciliação
                                             │
                                        IPC v1 loopback
                                   ┌─────────┴─────────┐
                            Deriv Worker        Simulated Worker
                            Demo financeiro     laboratório local
                            Real read-only
```

O pipeline financeiro obrigatório é:

```text
Strategy Runtime
→ Signal Arbiter
→ Portfolio Allocator
→ Risk Ledger
→ TradeIntent + RiskReservation + Outbox (commit)
→ Worker
```

## Requisitos locais

- Windows 10/11 64 bits;
- Python 3.13;
- PowerShell;
- dependências fixadas em `pyproject.toml`.

## Preparação do ambiente

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Validação canônica

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

## CLI local de soak

O CLI interno executa quatro cenários exclusivamente locais/read-only. Sem a flag ou variável de
opt-in ele encerra com código `2` e não cria diretório/relatório.

```powershell
python -m apps.core.soak_cli --run-soak-matrix --duration-seconds 5 --max-cycles 100 --max-reports 10
python -m apps.core.soak_cli --run-soak-matrix --profile fast --fault-preset heavy_load
```

O resultado fica, por padrão, em `reports/soak/soak_matrix_*.json`. A publicação usa temporário no
mesmo diretório, `fsync` e `os.replace`; a retenção mantém no máximo 10 relatórios e 20 MiB. O CLI
continua em `DECISION_ONLY`, usa sessões locais sintéticas e não aceita conta, credencial ou ordem.
Os perfis disponíveis são `fast`, `standard`, `extended` e `chaos`; fault presets são `none`,
`intermittent_crash`, `sleep_resume_gap` e `heavy_load`. Ciclos de injeção/recuperação entram no JSON
somente como tipo, ciclo, estado e reason code. O scanner aborta a publicação se detectar material
sensível.

Os testes comuns não usam rede nem credenciais. O único smoke externo atual usa market data pública
Deriv, permanece skipado por padrão e exige opt-in explícito:

```powershell
$env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC = "1"
python -m pytest tests/external/test_deriv_public_external.py -m external_deriv_public
Remove-Item Env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC
```

Esse smoke não autentica conta e não habilita ordem. Consulte [TEST_PLAN.md](TEST_PLAN.md).

## Estrutura do repositório

```text
apps/
├── auth_agent/         # identidade e lease simuladas
├── core/               # autoridade financeira e coordenação
├── deriv_worker/       # dados públicos, conta Demo financeira e Real read-only
└── simulated_worker/   # worker financeiro sintético da Fase 0
packages/
├── domain/             # modelos canônicos
├── persistence/        # SQLite, migrações, repositories e backup
├── protocol/           # IPC v1
├── strategies/         # runtime/checkpoint
├── strategy_catalog/   # manifesto, status e validação
├── signal_arbitration/ # conflitos e dedupe de sinais
├── portfolio_allocation/
├── market_data/ e market_pipeline/
├── identity/, licensing/ e security/
└── audit/, replay/ e observability/
tests/
├── unit/
├── contract/
├── integration/
├── replay/
├── chaos/
└── external/           # sempre opt-in
```

## Mapa da documentação

| Documento | Finalidade |
|---|---|
| [docs/README.md](docs/README.md) | índice consolidado da documentação v1.9.11 |
| [Visão geral atual](docs/PROJECT_OVERVIEW.md) | capacidades, limitações e estado real da versão |
| [Manual do usuário](docs/USER_GUIDE.md) | operação completa do aplicativo |
| [Arquitetura atual](docs/CURRENT_ARCHITECTURE.md) | processos, dados e fluxos implementados |
| [Estratégias e risco](docs/DERIV_STRATEGIES_AND_RISK.md) | regras estatísticas, radar e martingale |
| [Referência de componentes](docs/COMPONENT_REFERENCE.md) | mapa de módulos e responsabilidades |
| [Desenvolvimento/build/testes](docs/DEVELOPMENT_BUILD_AND_TEST.md) | setup, validação e geração de artefatos |
| [Solução de problemas](docs/TROUBLESHOOTING.md) | conexão, execução, risco e recuperação |
| [BRIEFING.md](BRIEFING.md) | visão executiva, estado e decisões centrais |
| [PRD](PRD_Trading_Desktop_Deriv_IQOption.md) | escopo, jornadas e requisitos FR/NFR |
| [Arquitetura](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md) | desenho técnico e estados |
| [AIGUARD.md](AIGUARD.md) | invariantes para IA e automação |
| [RULES.md](RULES.md) | regras normativas do projeto |
| [AGENTS.md](AGENTS.md) | fluxo de trabalho para agentes |
| [SECURITY.md](SECURITY.md) | ameaças, controles e tratamento de segredos |
| [TEST_PLAN.md](TEST_PLAN.md) | estratégia e gates de validação |
| [ROADMAP.md](ROADMAP.md) | fases e critérios de avanço |
| [CONTRIBUTING.md](CONTRIBUTING.md) | contribuição e revisão |
| [AUTHENTICATION_AND_LICENSING.md](AUTHENTICATION_AND_LICENSING.md) | identidade/licença da Fase 0 |
| [STRATEGY_PLATFORM.md](STRATEGY_PLATFORM.md) | catálogo, runtime, arbitragem e allocator |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | setup e rotina local |
| [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) | resposta operacional e safe stop |
| [docs/PERSISTENCE_AND_RECOVERY.md](docs/PERSISTENCE_AND_RECOVERY.md) | bancos, commit, backup e recovery |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | eventos, métricas e redação |
| [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md) | gates de build/release |
| [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | requisitos → implementação → testes |
| [docs/ERROR_AND_HEALTH_CODES.md](docs/ERROR_AND_HEALTH_CODES.md) | famílias de reason codes |
| [docs/IPC_PROTOCOL_V1.md](docs/IPC_PROTOCOL_V1.md) | contrato IPC interno |
| [docs/DERIV_WORKER.md](docs/DERIV_WORKER.md) | worker Deriv público, Demo financeiro e Real read-only |
| [docs/CLOSED_CANDLE_REPLAY.md](docs/CLOSED_CANDLE_REPLAY.md) | candle/journal/checkpoint/replay |
| [docs/MARKET_DATA_PIPELINE.md](docs/MARKET_DATA_PIPELINE.md) | scheduler, health e shadow |
| [WORKLOG.md](WORKLOG.md) | decisões e histórico append-only |

## Contribuição e incidentes

Use [CONTRIBUTING.md](CONTRIBUTING.md) para mudanças e [SECURITY.md](SECURITY.md) para incidentes.
Nunca publique senha, token, cookie, OTP, lease bruta, chave privada ou credencial de broker em
issue, log, fixture, screenshot ou pacote de suporte.
