# Arquitetura atual — Trading Lab Desktop v1.9.11

## 1. Princípio central

O Trading Core é a única autoridade financeira local. UI, estratégias e workers não podem alterar
diretamente ordens, reservas ou P&L. A aplicação divide responsabilidades em processos e aplica
falha fechada: quando falta evidência confiável, novas entradas são bloqueadas.

## 2. Árvore de processos

```text
TradingLab-Desktop-v1.9.11-*.exe       # lançador portátil C#
└── TradingLab.exe                     # Launcher Python/PyInstaller
    └── apps.core.runner               # Trading Core host
        ├── apps.auth_agent.runner     # identidade/lease local
        ├── apps.simulated_worker      # worker financeiro de laboratório
        ├── apps.deriv_worker          # integração Deriv isolada
        └── apps.ui                    # interface PySide6
```

O Launcher associa a árvore a um Windows Job Object. A morte abrupta do processo dono encerra os
descendentes. O fluxo normal, porém, usa shutdown ordenado.

## 3. Lançador portátil

Arquivo-fonte: `build_scripts/PortableLauncher.cs`.

Responsabilidades:

- mutex global ao usuário `Local\TradingLabDesktop.SingleInstance`;
- detectar segunda abertura;
- restaurar/trazer a janela existente para frente;
- extrair `TradingLab.payload.zip` incorporado para pasta temporária única;
- iniciar o `TradingLab.exe` interno;
- aguardar o término;
- remover a pasta temporária quando possível.

O lançador portátil não lê banco, não recebe token Deriv e não executa estratégia.

## 4. Launcher Python

Pacote: `apps/launcher`.

Responsabilidades:

- escolher perfil e workers;
- verificar `release_manifest.json` no build congelado;
- adquirir `profile.lock`;
- criar tokens efêmeros de IPC;
- iniciar o Core e, depois de `READY`, a UI;
- monitorar saúde da árvore;
- reiniciar somente componentes permitidos e de forma limitada;
- executar shutdown seguro;
- impedir segunda instância no mesmo perfil.

O perfil padrão do build congelado é
`%LOCALAPPDATA%\TradingLab\profiles\default`. Em execução pelo código-fonte, o padrão é
`data/profiles/default`, a menos que `--profile-dir` seja informado.

## 5. Trading Core

Pacote: `apps/core`.

O Core compõe:

- `CoreRuntime`: banco, recovery, workers, risk e eventos;
- `CoreLifecycleService`: startup, conexão Deriv, safe stop e shutdown;
- `CoreUiProjectionService`: projeções e comandos da UI;
- `OrderCoordinator`: persistência e despacho financeiro;
- `RiskLedger`: exposição, P&L e limites;
- `HealthGate`: bloqueios globais e por broker/conta;
- `DerivTelemetryMonitor`: saldo, relógio, ticks, estratégias e radar;
- `DerivDigitAutoTrader`: transformação de sinal Demo em ordem;
- `BrokerEventPump`: processamento de eventos financeiros;
- `ReconciliationCoordinator`: resolução por evidência;
- `TradingReadinessSnapshot`: disponibilidade, recovery, readiness e ARM separados;
- supervisores de worker e Auth Agent;
- serviços de replay, shadow, soak e diagnóstico.

O Core nunca recebe o token Deriv em texto como parâmetro de comando financeiro. Ele conhece o
diretório do vault e fornece essa referência ao worker autenticado.

## 6. UI

Pacote: `apps/ui`.

A UI:

- recebe snapshots imutáveis via IPC;
- envia apenas comandos limitados;
- atualiza a projeção a cada 500 ms;
- tenta reconectar o IPC após falha transitória;
- abre o helper de login Deriv em subprocesso isolado;
- não acessa SQLite;
- não conecta diretamente ao websocket Deriv;
- não calcula stake autoritativa;
- não resolve estado financeiro.

Comandos disponíveis no canal UI:

- obter projeção;
- Safe Stop;
- retomar novas entradas;
- solicitar encerramento seguro;
- conectar a conta Deriv salva;
- atualizar configuração de risco de dígitos;
- gerar diagnóstico.

## 7. Auth Agent

Pacote: `apps/auth_agent`.

O Auth Agent administra o modelo de identidade/licença do produto:

- login e OTP simulados;
- device identity;
- lease assinada;
- autorização reduzida para nova entrada;
- vault de usuário;
- IPC autenticado.

Ele não recebe token, cookie ou credencial da Deriv. A implementação comercial de identidade remota
não está configurada; o serviço atual é local/simulado para demonstrar fronteiras e falhas.

Falha do Auth Agent bloqueia novas entradas, mas não deve interromper liquidação ou reconciliação de
ordem já aberta.

## 8. Deriv Worker

Pacote: `apps/deriv_worker`.

Responsabilidades:

- REST de descoberta de contas e obtenção de OTP;
- websocket público, Demo ou Real;
- validação estrita de host, TLS, path, query e tipo de conta;
- allowlist de operações por modo;
- normalização de símbolos, contratos, ticks, candles, saldo e relógio;
- proposal/buy e acompanhamento de contrato em Demo;
- reconciliação por contrato, statement e profit table;
- subscriptions e detecção de gap/duplicidade;
- publicação de eventos normalizados ao Core.

Modos:

| Transporte | Uso | Ordem |
|---|---|---|
| `fake-public` | padrão local seguro | não |
| `fake-demo` | testes locais | conforme harness |
| `live-public` | dados públicos externos | não |
| `live-demo` | conta Demo autenticada | sim |
| `live-real` | conta Real autenticada | não |

Mesmo existindo código comum de sessão, o Core inicia `live-real` com
`allow_real_financial_submission=False` e não anexa o auto trader.

## 9. Simulated Worker

Pacote: `apps/simulated_worker`.

É um worker financeiro local usado para provar:

- IPC e handshake;
- persist-before-act;
- aceite, rejeição e timeout ambíguo;
- eventos de ordem;
- crash e reconciliação;
- isolamento de processo.

Seu banco externo simulado é separado do `state.db`. Ele não representa uma conta de corretora.

## 10. IQ Option Worker

Pacote: `apps/iqoption_worker` e `packages/brokers/iqoption`.

Existem contratos, validadores, sessão/harness de ordem e reconciliação para testes. A CLI pública do
Launcher não oferece `iqoption` como worker selecionável e a aplicação não implementa login ou
sessão externa. Portanto, esta área é infraestrutura de laboratório, não produto operacional.

## 11. IPC v1

Todos os canais críticos usam TCP loopback com framing JSON. Características:

- `PROTOCOL_VERSION = 1`;
- frame máximo de 64 KiB;
- envelope com ID, correlação, papel de origem/destino, deadline e tipo;
- validação exata de campos;
- tokens efêmeros de 256 bits entregues por `stdin`;
- prova HMAC em canais de controle;
- payloads imutáveis e limitados;
- sem `pickle` ou desserialização arbitrária;
- replay de mensagens tratado de forma idempotente ou rejeitado em conflito.

Os tokens de IPC não aparecem em argv, logs, projeções ou banco.

## 12. Pipeline financeiro

```text
Sinal estatístico
  → seleção da estratégia ativa
  → filtro de edge/desempenho
  → Portfolio Allocator / martingale delimitado
  → autorização reduzida
  → Health Gate
  → Risk Ledger
  → transação SQLite:
       TradeIntent
       RiskReservation
       OutboxMessage
       Order local
  → dispatch por broker/conta
  → Deriv Worker
  → evento normalizado
  → transação de settlement/P&L/liberação
```

Comandos financeiros são serializados por broker e conta. O banco também impõe restrições como
linha de defesa adicional.

## 13. Estados e ambiguidade

Estados terminais comuns:

- `SETTLED`;
- `REJECTED`;
- `CANCELLED`.

Estados não terminais/ambíguos podem incluir `PENDING`, `ACCEPTED`, `OPEN`, `UNKNOWN` e
`SETTLEMENT_UNKNOWN`.

Se um timeout ocorrer depois de um possível envio:

1. o resultado vira `UNKNOWN`;
2. a reserva permanece ativa;
3. o Health Gate do escopo fecha;
4. o comando não é reenviado automaticamente;
5. a reconciliação procura evidência externa.

## 14. Health Gate

O `HealthGate` possui bloqueios globais e por broker/conta. Uma nova entrada exige que o snapshot
efetivo esteja aberto. Famílias de bloqueio incluem:

- Safe Stop;
- banco/integridade/escrita;
- worker desconectado ou incompatível;
- ordem ambígua;
- reconciliação pendente;
- relógio ou market data não confiável;
- Stop Loss/Take Profit/cooldown;
- autorização/licença/token;
- exposição global ou por símbolo.

Retomar o bot limpa somente `HG_SAFE_STOP`; nenhum outro blocker é removido por conveniência.

O estado lifecycle `READY` significa que o control plane Core/UI está disponível. Não significa que
o bot está armado. A UI projeta `DERIV_READY_TO_ARM`; `ready_to_trade` só é verdadeiro quando todos
os pré-requisitos estão provados e o usuário armou explicitamente uma nova execução.

## 15. Persistência

### state.db

Banco financeiro autoritativo, escrito exclusivamente por `SingleDatabaseWriter`:

- `trade_intents`;
- `risk_reservations`;
- `outbox_messages`;
- `orders`;
- `processed_order_events`;
- `broker_order_events`;
- `reconciliation_evidence`;
- `reconciliation_attempts`;
- `digit_risk_runtime`;
- `schema_migrations`.

Settlement e reconciliação de dígitos atualizam `digit_risk_runtime` dentro da mesma transação que
aplica P&L e libera a reserva. A configuração continua no JSON atômico, mas o estado corrente da
sequência, o ativo pinado e a origem UTC do cooldown não dependem mais de memória.

### Journal operacional

`operational-journal.jsonl` é bounded, rotacionado e redigido. Serve para reconstruir lifecycle,
recovery, gates e ARM/DISARM após crash; não substitui `state.db` nem autoriza decisão financeira.

### Recovery vivo

Queda de worker desarma novas entradas, substitui o cliente atrás de uma porta estável e agenda
backoff/circuit probe. Sucesso reconcilia candidatos e termina em `READY_TO_ARM`, nunca em ARM
automático. No startup, candidato Deriv não terminal com credencial salva dispara recuperação
autenticada em background sem impedir a UI de abrir.

### strategy_data.db

Banco separado para dados e evidência de estratégia:

- `candles`;
- `decision_events`;
- `replay_runs`;
- `warmup_checkpoints`;
- `strategy_validation_reports`;
- `strategy_schema_migrations`.

### simulated_broker_state.db

Banco do simulador externo:

- `external_orders`;
- `broker_metrics`;
- `simulated_broker_events`.

SQLite usa WAL, foreign keys, migrações com checksum, marker de existência esperada e conexões de
leitura query-only.

## 16. Market data, replay e shadow

O repositório também contém uma plataforma genérica de candles:

- ingestão somente de candle fechado;
- repositório separado de dados;
- backfill paginado com overlap;
- scheduler por relógio monotônico;
- health por série;
- journal append-only;
- checkpoint de warm-up;
- replay determinístico;
- runtime `DECISION_ONLY`;
- roteador live compartilhado;
- sessões shadow multi-série;
- host com limites de CPU/RSS/lag;
- soak temporal com injeção de falhas.

Essa plataforma é usada principalmente como infraestrutura de pesquisa/teste. O auto trader de
dígitos live usa o stream de ticks e o motor específico de dígitos.

## 17. Shutdown

O encerramento normal segue a ordem:

```text
Safe Stop
→ drain de eventos financeiros já recebidos
→ parar auto trader e telemetria
→ parar workers
→ parar Auth Agent
→ parar UI service e Core
→ fechar SQLite
→ liberar locks e Job Object
```

Timeouts escalam de espera para terminate e, por fim, kill. Mesmo assim, o próximo startup trata a
morte como abrupta e executa recovery/reconciliação.

## 18. Integridade da distribuição

O build onedir contém `release_manifest.json` com tamanho e SHA-256 de cada arquivo rastreado. No
startup congelado, arquivo ausente, extra ou modificado falha antes de iniciar subprocessos. O
manifesto é auto-hashado, mas ainda não é assinatura Authenticode nem substitui uma cadeia de
confiança de distribuição.
