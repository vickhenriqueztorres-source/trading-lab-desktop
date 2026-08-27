# AGENTS — Instruções para Agentes do Repositório

**Baseline obrigatória:** v1.9.11
**Atualizado em:** 2026-08-26

## 1. Contexto

Este repositório contém o Trading Lab Desktop, um aplicativo Windows local que executa estratégias
na Deriv Demo. Deriv Real é read-only e a IQ Option possui infraestrutura/testes, mas ainda não tem
login ou execução externa operacional no aplicativo.

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
├── UI
├── Auth Agent
│   ├── PKCE / sessão
│   ├── Token Vault
│   ├── Device Identity
│   └── Offline Lease
├── Trading Core
│   ├── Command Bus
│   ├── Event Bus
│   ├── State Machines
│   ├── Strategy Catalog Local
│   ├── Strategy Runtime
│   ├── Signal Arbiter
│   ├── Portfolio Allocator
│   ├── Risk Ledger
│   ├── Durable Outbox
│   ├── Single Database Writer
│   ├── Recovery Coordinator
│   ├── Health Gate
│   ├── Monotonic Market Backfill Scheduler
│   ├── Market Health Gate
│   ├── Accepted Candle Dispatcher [DECISION_ONLY]
│   ├── Continuous Shadow Runtime [poll-driven]
│   ├── Supervised Shadow Lifecycle [explicit recovery]
│   ├── Bounded Shadow Host [fairness + circuit + budgets]
│   ├── Broker Shadow Soak Runner [Core + child telemetry]
│   ├── Soak Matrix CLI [explicit opt-in + atomic reports]
│   └── Worker Supervisor
│       └── IPC v1 (TCP loopback, framed JSON)
│           └── Simulated Financial Worker Process
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

O Core é o único dono do estado financeiro. Workers traduzem protocolos. UI apresenta projeções. Estratégias apenas geram sinais. O Auth Agent administra sessão/dispositivo/lease sem receber credenciais de corretora. O plano de controle remoto não executa trades. Signal Arbiter e Portfolio Allocator precedem o Risk Ledger.

Na implementação atual **v1.9.11**, o Deriv Worker possui quatro capacidades distintas e
explicitamente separadas: dados públicos, sessão Demo autenticada, execução financeira Demo e
sessão Real somente leitura. O transporte `fake-public` permanece o padrão de startup. A conexão do
usuário ocorre depois de abrir a UI, exclusivamente por API Token/PAT, seleção da conta retornada
pela API oficial e cofre DPAPI CurrentUser. Conta Real exige seleção e confirmação explícitas, mas o
Core não concede capability financeira Real nesta versão.

Em Deriv Demo, as estratégias `Tail Probability Edge`, `Selective Differs Edge` e
`Parity Regime Edge` podem produzir ordens de um tick depois de 500 ticks de aquecimento, filtros
conservadores, seleção explícita da estratégia, armamento pelo botão **Ligar Bot**, autorização,
Health Gate e Risk Ledger. O auto trader permite uma ordem em voo, consome cada sinal apenas uma
vez, suporta seleção automática entre R_10/R_25/R_50/R_75/R_100, filtro de desempenho com cooldown
temporário e Bounded Martingale opcional. Intenção, reserva, outbox e ordem local são persistidas
antes do dispatch; timeout potencialmente aceito permanece `UNKNOWN` e nunca recebe retry cego.

A sessão autenticada é supervisionada. Queda fecha novas entradas, interrompe o executor, substitui
o worker, obtém OTP novo, restaura telemetria/subscriptions e reconcilia ordens não terminais. A
conexão inicial possui três tentativas limpas; a recuperação posterior usa backoff limitado. O
dashboard recebe resultados confirmados por polling IPC resiliente de 500 ms. O diagnóstico local
gera ZIP redigido, limitado e escaneado contra segredos.

O Auth Agent ainda usa serviço de identidade/licenciamento simulado local. Ele roda em subprocesso
com IPC loopback autenticado, token efêmero e prova HMAC. No Windows, o vault usa DPAPI CurrentUser,
DACL por SID e escrita atômica. Queda do Auth Agent bloqueia apenas novas entradas; eventos,
reconciliação e liquidação permanecem no Core. A credencial Deriv nunca cruza para o serviço de
identidade.

Strategy Catalog, runtime de candle fechado, Signal Arbiter, Portfolio Allocator, journal,
checkpoint, replay, backfill, market health, shadow host e soak bounded continuam disponíveis como
plataforma local de pesquisa e validação. `python -m apps.core.soak_cli` exige opt-in explícito,
executa cenários locais/read-only, publica JSON atomicamente e aplica retenção bounded. O restore
drill opera somente em perfil temporário isolado.

O Launcher executa a árvore sob `profile.lock`, guard independente do Core, mutex de instância do
portátil e Windows Job Object. O canal lifecycle solicita Safe Stop, drain e shutdown sem abrir o
banco. Kill do Core/Launcher encerra descendentes e o próximo startup executa recovery e
reconciliação. IQ Option possui modelos, worker/harnesses e testes de isolamento, mas não possui
login nem integração externa operacional no aplicativo v1.9.11.

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

A árvore acima resume os limites lógicos; consulte
`docs/COMPONENT_REFERENCE.md` para o mapa executável por arquivo. Não crie diretórios vazios só
para imitar uma arquitetura futura. Introduza cada pacote quando houver código e teste que o
justifiquem.

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

As dependências de desenvolvimento ficam no extra `dev`. Testes externos continuam opt-in e
separados dos testes locais; a suíte local usa somente SQLite temporário, subprocessos locais,
workers simulados e transportes fake para Deriv. Qualquer teste Deriv externo deve ser marcado como
`external_deriv_public` ou `external_deriv_demo` e permanecer explicitamente opt-in.

## 7. Convenções de implementação

- Python com type hints em fronteiras públicas.
- `dataclass(frozen=True)` ou modelo imutável para mensagens/eventos.
- `Decimal` ou minor units para valores financeiros.
- UTC para persistência e monotonic clock para duração.
- enums explícitos para estados e motivos de bloqueio.
- códigos de erro estáveis para UI e suporte.
- payload externo validado antes de virar modelo de domínio.
- dependência da IQ confinada ao worker IQ.
- dependência de UI confinada ao app UI.
- SQL e migrações concentrados no pacote de persistência.
- funções de estratégia sem efeitos externos.
- cliente desktop sem `client_secret` confiável ou segredo mestre embutido.
- Device ID aleatório com chave própria; não usar hardware fingerprint como autenticação principal.
- material sensível de sessão/dispositivo protegido no escopo do usuário do Windows.

## 8. Limites de alteração

### Core

- não importar SDK/biblioteca de corretora;
- não armazenar senha/token;
- não inferir semântica específica sem capability/mapper;
- serializar comandos financeiros por conta;
- persistir antes de despachar.

### Workers

- não decidir risco;
- não gravar banco crítico;
- não executar estratégia;
- não fazer retry cego de ordem;
- emitir eventos normalizados e preservar origem redigida;
- negociar versão de protocolo no startup.

### UI

- não acessar corretora;
- não acessar SQLite diretamente;
- não manter estado financeiro autoritativo;
- não confundir real e practice;
- não bloquear o Core quando fechar ou travar.

### Estratégias

- não conhecer credenciais, saldo ou API;
- receber dados já validados;
- retornar sinal com validade e evidência;
- manter estado isolado por contexto;
- compartilhar código somente quando a semântica permitir.

### Identidade e licenciamento

- manter UX única de e-mail + código de seis dígitos;
- usar `user_id` estável internamente e não como credencial do cliente;
- tratar desktop como cliente público e usar PKCE quando aplicável;
- não embutir `client_secret`, segredo mestre ou chave privada de assinatura no executável;
- emitir/rotacionar tokens e permitir revogação/reautenticação;
- gerar `device_id` aleatório e chave própria; não autenticar por serial/MAC/fingerprint;
- proteger refresh token, chave privada e lease no escopo do usuário do Windows;
- verificar assinatura, validade, dispositivo, compatibilidade e entitlements da lease;
- bloquear novas entradas quando entitlement/lease expirar ou for revogado;
- nunca interromper acompanhamento de ordens por falha de licença;
- não enviar credenciais, cookies, tokens de broker ou histórico completo ao serviço de identidade;
- a v1.9.11 usa API Token/PAT Deriv protegido por DPAPI e separado da identidade do produto;
  qualquer fluxo OAuth futuro exige decisão e testes próprios; sessão IQ permanece no IQ Worker.

### Catálogo e multi-estratégias

- verificar manifesto, hash, assinatura quando aplicável, compatibilidade, status e entitlement antes de carregar;
- versionar código e parâmetros juntos;
- isolar runtime por estratégia + versão + broker + conta + produto + ativo + timeframe;
- executar Signal Arbiter antes do Portfolio Allocator e Risk Ledger;
- sinais opostos no mesmo contexto resultam em nenhuma entrada no MVP;
- não somar stakes de sinais coincidentes;
- aplicar orçamento por estratégia/conta/global antes da reserva;
- manter evidências de backtest, walk-forward, replay e practice por versão;
- tratar estratégia suspensa como proibida para novas entradas;
- não executar Python/código arbitrário baixado no MVP;
- tratar Tail Probability Edge, Selective Differs Edge e Parity Regime Edge como experimentais,
  nunca como garantia de resultado;
- ao trocar estratégia, executar Safe Stop, limpar sinal pendente, resetar contexto incompatível e
  exigir novo sinal antes de permitir ordem;
- manter Martingale opt-in, limitado e fixado ao ativo durante a sequência de recuperação.

## 9. Estratégia de testes por mudança

| Mudança | Testes mínimos |
|---|---|
| Modelo de domínio | unidade, serialização e compatibilidade |
| Estado de ordem | transições válidas/inválidas, duplicidade e fora de ordem |
| Risk Ledger | concorrência, limites e propriedades |
| Banco/migração | upgrade, restart, I/O error e integridade |
| IPC | framing, tamanho, versão, checksum e processo morto |
| Worker | contract test, timeout, reconexão e payload inesperado |
| Estratégia Digit Edge | determinismo, 500 ticks de warm-up, consumo único, seleção de ativo e replay |
| Martingale | desligado por padrão, limites, stop projetado, pin de ativo e reset por ganho |
| Sessão Deriv | reconnect, OTP novo, restore de ticks, reconciliação e ausência de rearm/retry |
| UI | projeção, modo real/practice, bloqueios e parada segura |
| Atualizador | assinatura, adulteração, interrupção e rollback |
| Identidade/licença | OTP/PKCE simulado, rotação, revogação, dispositivo, lease adulterada/expirada e offline |
| Catálogo/arbiter | manifesto/hash/status/entitlement, sinais opostos/iguais, suspensão e orçamento |

## 10. Integrações externas

- Use simuladores por padrão.
- Use servidor/provedor de identidade simulado por padrão; nunca use código OTP, token ou sessão real em fixture.
- Qualquer teste financeiro Deriv deve usar exclusivamente Demo e opt-in explícito.
- Qualquer teste IQ Option deve começar em practice.
- Não envie ordem Real: a v1.9.11 permite somente conexão/monitoramento read-only.
- Não inclua segredos em comando, screenshot ou saída de ferramenta.
- Não trate ausência de resposta como rejeição.

## 11. Atualização do WORKLOG

Toda mudança material deve acrescentar uma entrada contendo:

- data e identificador;
- objetivo;
- requisitos relacionados;
- arquivos alterados;
- decisões;
- validações executadas;
- riscos ou limitações;
- próximo passo.

Não reescreva entradas históricas para fazer o trabalho parecer concluído. Corrija com uma nova entrada.

## 12. Critério de conclusão do agente

Uma tarefa só está concluída quando:

- atende ao pedido e aos requisitos relacionados;
- preserva `AIGUARD.md` e `RULES.md`;
- possui validação proporcional ao risco;
- não deixa estados financeiros implícitos;
- não expõe segredos;
- não envia credenciais de corretora ao plano de identidade/licenciamento;
- preserva acompanhamento de ordens abertas diante de expiração/revogação;
- valida proveniência e arbitragem de estratégias quando aplicável;
- atualiza o worklog quando material;
- documenta o que não foi possível validar;
- fornece um próximo passo claro quando houver trabalho restante.
