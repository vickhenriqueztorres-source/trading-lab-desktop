# Market Data Pipeline — Scheduler, Health Gate e Shadow

**Projeto:** DualTrade Desktop  
**Estado:** Deriv read-only, scheduler determinístico e estratégia em `DECISION_ONLY`

## Autoridade e fronteiras

O Trading Core permanece a única autoridade financeira. O pipeline deste documento não possui
capacidade de submissão e não cria estado no `state.db`.

```text
Deriv Worker read-only
    → IPC v1 history paginado + MARKET_TICK_EVENT
    → MarketBackfillScheduler (monotonic)
    → BackfillPlanner (overlap + batch bounded)
    → DerivCandleHistoryPump
    → CandleIngress
    → strategy_data.db
    → continuity validation
    → MarketHealthGate por série
    → AcceptedCandleDispatcher
    → Strategy Pipeline [DECISION_ONLY, dispatch=False]
```

O scheduler solicita dados. O ingress valida e persiste. O gate autoriza a entrega. O dispatcher
chama o pipeline. Nenhuma dessas responsabilidades é fundida.

## Identidade da série

`MarketSeriesId` inclui broker, símbolo da corretora, símbolo canônico, produto, timeframe e
contexto. Dados públicos usam o contexto explícito `PUBLIC_MARKET`; eles não inventam uma conta
demo. Saúde e recovery lock são isolados por essa identidade completa.

## Clock e horizonte confiável

Intervalos, staleness, suspensão, retry e `next_due` usam somente `MonotonicClock`. Valores
monotônicos não são persistidos e são recalculados no boot a partir dos candles duráveis.

O planner só avança quando recebe `TrustedClosedHorizon`, derivado do horário da fonte/broker. A
ausência dessa evidência produz `CLOCK_UNTRUSTED`; o relógio de parede local nunca prova que um
candle da corretora fechou.

Na composição do aplicativo, `DerivTelemetryMonitor` consulta o relógio pelo IPC read-only em
intervalos bounded e mantém somente o último snapshot imutável no Core. A confiança exige RTT de no
máximo 1.000 ms e offset absoluto de no máximo 2.000 ms. Timeout, payload inválido, crash/restart ou
limite excedido acrescenta `MD_CLOCK_UNTRUSTED` ao Health Gate global de entradas. O monitor antigo
é encerrado antes do restart do worker; a nova geração precisa produzir evidência válida para
limpar exclusivamente esse blocker.

No modo demo autenticado, o mesmo monitor consulta o saldo oficial por uma mensagem IPC distinta.
O saldo serve apenas à projeção UI e nunca entra no Portfolio Allocator, Risk Ledger, stake ou
estado financeiro. O transporte público não possui essa autoridade e projeta saldo indisponível.

## Planner, overlap e paginação

`BackfillPlanner` é puro e não chama transporte. A entrada é a fronteira durável, o histórico
mínimo do manifesto, o timeframe e o horizonte confiável. Cada janela possui:

- `MAX_CANDLES_PER_BATCH` configurável;
- `backfill_overlap_candles` configurável, com default conservador de 2;
- início/fim alinhados ao timeframe;
- `end_epoch` explícito para paginação histórica Deriv;
- cursor recalculado depois de cada commit do ingress.

Warm-up maior que um lote começa pela janela mais antiga necessária e avança cronologicamente.
Recovery nunca carrega todo o histórico em memória. Overlap idêntico vira `DUPLICATE`; conteúdo
divergente para a mesma identidade continua sendo `CANDLE_CONFLICT` fail closed.

## Scheduler monotônico

`MarketBackfillScheduler.tick()` é determinístico e não cria thread ou `sleep`. Ele implementa:

- uma agenda efêmera por série;
- coalescing de triggers da mesma série;
- recovery lock explícito por série;
- limite global de jobs por tick;
- rotação justa entre séries;
- retry exclusivo de leitura por `ReadOnlyBackfillRetryPolicy`;
- backoff exponencial, jitter injetável, teto e máximo de tentativas;
- reset do contador após sucesso.

Esgotamento deixa a série em `FAILED/MD_HISTORY_EXHAUSTED`. Não existe helper genérico de retry de
broker e nenhum comando financeiro é importado pelo scheduler.

## Estados de saúde

O gate mantém uma projeção independente por série:

```text
INITIALIZING
WARMING_UP
HEALTHY
STALE
GAPPED
BACKPRESSURED
RECONNECTING
CLOCK_UNTRUSTED
INCOMPATIBLE
FAILED
```

Somente `HEALTHY` permite entrega shadow. O snapshot inclui reason code estável, última fronteira
durável, última proveniência, gaps, backpressure, geração de reconnect, progresso de warm-up e
`dispatch_allowed`. `BrokerMarketHealth` é uma derivação das séries ativas e não substitui o gate
individual.

## Gap e backpressure

Continuidade de timeframe fixo exige:

```text
current.open_time_ms == previous.close_time_ms
```

O sistema não cria candles artificiais nem faz forward-fill. Gap produz `GAPPED`, mantém delivery
bloqueado e agenda overlap. `HEALTHY` só retorna quando a janela mínima termina exatamente no
horizonte confiável e toda a sequência está contínua.

Overflow produz `BACKPRESSURED`. Esvaziar uma fila não limpa o estado. É obrigatório fazer novo
backfill, deduplicar e revalidar continuidade antes de limpar backpressure.

## Reconnect, restart e suspensão

Disconnect ou suspensão incrementam `reconnect_generation`. A sequência é:

```text
disconnect/suspend
→ RECONNECTING ou STALE
→ protocolo/clock válidos
→ backfill overlap da geração atual
→ continuidade comprovada
→ restauração de stream coordenada
→ HEALTHY
```

Resposta de geração antiga não muda o health atual. Candles canônicos já persistidos continuam
idempotentes, mas a geração corrente ainda precisa executar overlap. Socket conectado ou banco já
preenchido, isoladamente, nunca reabrem o gate.

Uma lacuna monotônica grande representa uma única suspensão: timers atrasados são descartados e a
agenda é recalculada a partir da boundary durável. No restart, nenhum `next_due_monotonic` antigo é
restaurado.

## Candle parcial

O planner pode receber uma resposta que contenha o candle corrente. O adapter/ingress o contabiliza
e descarta antes do domínio fechado. As invariantes são:

```text
partial_persisted = 0
partial_dispatched = 0
strategy_decisions_from_partial = 0
```

## Entrega shadow

`AcceptedCandleDispatcher` recebe apenas `ClosedCandle`, confirma que o mesmo candle já existe no
repositório durável e consulta o gate da série. O call site passa `dispatch=False` explicitamente.

`ExecutionCapabilityGate` fornece a segunda defesa: o default é `DECISION_ONLY` e
`can_submit_orders=false`; `dispatch=True`, `SIMULATED_EXECUTION` ou `BROKER_EXECUTION` são
rejeitados com `CAPABILITY_DENIED` nessa composição.

O pipeline reutiliza Runtime → Arbiter → Allocator → Risk Ledger sintético e o commit atômico do
journal/checkpoint. Intents encontrados no journal são decisões sintéticas; não são `TradeIntent`
financeiro, não criam `RiskReservation` no `state.db`, não criam Outbox e não chegam ao worker.

## Shadow contínuo: histórico e stream

`ContinuousShadowRuntime` é dirigido por `poll_once()` e não cria thread oculta. No startup e em
cada geração de reconnect, ele executa o scheduler/backfill primeiro e só restaura a subscription
quando o snapshot da série está `HEALTHY`. Um stream conectado não é evidência suficiente para
liberar a entrega.

Ticks validados são agregados pelo Core em candles de timeframe fixo com preços convertidos de
`Decimal` para integer units na escala configurada. Open/high/low/close nunca usam `float`. O
agregador possui deduplicação limitada, rejeita precisão excedente, torna tick fora de ordem
explícito e não cria candle artificial para preencher lacuna.

Histórico e candles fechados pelo stream terminam no mesmo `CandleIngress`. Somente o resultado
durável `ACCEPTED` alcança o dispatcher; redelivery idêntico vira `DUPLICATE`, e conflito, gap ou
out-of-order fecham o gate e acionam novo backfill. Disconnect descarta o bucket parcial, incrementa
`reconnect_generation` e impede consumo até a recuperação dessa geração. Exceção de recepção marca
`RECONNECTING` antes de ser propagada ao supervisor. Timeout stale cancela a assinatura, descarta o
bucket parcial e exige nova recuperação; nenhum dos casos aplica retry cego ao stream.

A referência opcional de replay compara, por fechamento, hash encadeado, quantidade de sinais e
quantidade de decisões. Divergência produz `FAILED/MD_SHADOW_DIVERGENCE`; ela não é apenas uma
métrica informativa. A prova integrada combina 400 candles de histórico e 100 do stream e termina
idêntica ao replay limpo de 500 candles.

## Composição supervisionada no Core

`SupervisedShadowRuntime` coordena o `ReadOnlyWorkerSupervisor` já existente e o runtime shadow sem
criar uma segunda thread de scheduling. Seu lifecycle explícito é:

```text
STOPPED → STARTING → RUNNING
                     ↓ worker loss/poll failure
                  RECOVERING
                     ↓ restart + backfill da geração atual
                  RUNNING
```

O serviço não reinicia silenciosamente dentro de `poll_once()`. Perda observada bloqueia o runtime,
e `recover()` explícito substitui o cliente IPC, reconstrói coordinator/scheduler a partir do banco,
executa overlap e somente então restaura a subscription. Falha de startup ou recovery permanece
`FAILED`/`RECOVERING`; conexão do socket isoladamente não libera candles.

O snapshot imutável expõe estado do serviço/worker, subscription, tentativas de start/recovery,
polls/falhas, duração monotônica e maior atraso live observado. Não contém candle, credencial,
saldo ou estado financeiro.

A prova com subprocesso real do projeto mata o worker Deriv fake, confirma novo PID, overlap
idempotente e geração de reconnect antes de fechar o próximo candle live pelo IPC. O supervisor
continua validando `can_submit_orders=false` no handshake.

## Host bounded de múltiplas séries

`ShadowRuntimeHost` registra séries pela identidade completa `MarketSeriesId` e executa ciclos
caller-driven com limite de ações e timeout por poll. A rotação do cursor fornece fairness; uma
série em recovery não impede polls de outra série saudável. Não existe backlog de callbacks nem
fila ilimitada criada pelo host.

Cada série possui `CrashCircuitBreaker` e `RestartPolicy` reutilizados do supervisor: falha agenda
backoff exponencial monotônico com jitter validado, repetição abre o circuito e somente o estado
`HALF_OPEN` permite nova prova de recovery. Recovery continua estritamente read-only e termina no
backfill/continuity/subscription restore do serviço.

O host publica snapshot imutável com ciclos, ações, falhas, tentativas de recovery, circuit state e
próximo instante monotônico por série. `SystemResourceProbe` mede CPU do processo Core e RSS
(Working Set no Windows). Limites opcionais de CPU por ciclo, RSS e lag live falham fechado:
`RESOURCE_EXHAUSTED` executa shutdown de todos os serviços shadow. Métricas não carregam payload de
candle, segredo ou estado financeiro.

O soak determinístico executa 10.000 ciclos e 20.000 ações em três séries, com diferença máxima de
um poll entre elas. A integração IPC real passa pelo host, mata o worker e comprova que o backoff
vence antes do restart/backfill/restore.

## Stream compartilhado por sessão

`SharedMarketTickRouter` é a primeira composição broker-level para dados live: ele recebe um único
`LiveTickSource` compartilhado, registra séries completas e entrega uma `RoutedLiveTickSource` para
cada runtime. O router não cria thread, não inicia worker, não persiste estado financeiro e não
executa estratégia. Ele apenas lê a fila única do cliente IPC quando um runtime faz poll e
demultiplexa ticks por `subscription_id` validado contra broker e símbolo.

Cada série possui uma fila pequena e bounded. Se um tick de outra série chegar durante o poll atual,
ele é roteado para a fila da série correta; se a fila estiver cheia, o router levanta
`MD_BACKPRESSURE`. Tick com broker, símbolo ou subscription desconhecida levanta
`MD_SCOPE_MISMATCH`. Esses erros são intencionais: dado que não pode ser roteado com prova não deve
chegar ao agregador nem à estratégia.

A prova de integração usa um único subprocesso Deriv fake, um único `SocketWorkerClient`, duas
subscriptions e dois `ContinuousShadowRuntime`. Um runtime pode observar primeiro um tick da outra
série sem roubá-lo; ambos fecham candles pelo mesmo IPC e a capability continua
`can_submit_orders=false`.

## Sessão broker-level read-only

`BrokerShadowSession` envolve o supervisor read-only, o cliente IPC e o router em uma única
composição por broker. A sessão registra várias séries completas antes do start, inicia o worker uma
única vez, cria um runtime por série com uma `RoutedLiveTickSource` e alterna polls entre séries em
ordem justa. O snapshot mostra estado, health do worker, subscriptions, contadores de polling,
router e lag máximo por série; não contém candle bruto, saldo, credencial ou estado financeiro.

Quando o worker deixa de estar `READY`, a sessão chama `on_disconnect()` em todos os runtimes
subscritos, move o estado para `RECOVERING` e não tenta recuperar dentro do poll. `recover()` é
explícito: ele para a geração antiga, executa um único `supervisor.restart()`, recria router e
runtimes e chama `recover_and_restore()` em todas as séries. A subscription só volta dentro do
contrato já existente do runtime, depois de o scheduler/backfill da série reabrir o Market Health.

Na prova IPC real, duas séries Deriv compartilham o mesmo subprocesso; após kill, ambas ficam
`RECONNECTING`, um novo PID sobe uma única vez e as duas subscriptions são restauradas. Essa
composição continua read-only e não adiciona `ORDER_SUBMIT`, outbox ou dispatch financeiro.

## Soak broker-level bounded

`BrokerShadowSoakRunner` hospeda uma `BrokerShadowSession` como unidade broker-level bounded. O
runner é caller-driven e finito: `max_cycles`, `poll_timeout_seconds`, `max_recoveries`, budget de
RSS do Core, RSS do subprocesso e lag live são explícitos. O snapshot imutável agrega contadores de
ciclo, polls, falhas, recoveries, health/subscriptions da sessão e amostras de recurso do Core e do
worker filho. Ele não armazena candle bruto, saldo, credential, intenção de trade ou payload externo
de broker.

Em cada ciclo, o Core mede recursos, executa uma ação da sessão (`poll_once()` ou `recover()`),
mede novamente e deriva estado. Se o worker cair, a sessão entra em `RECOVERING`; o runner só chama
`recover()` enquanto o limite de recoveries permitir. Exceder budget ou limite de recovery encerra
a sessão e retorna reason code estável. Esse fechamento bloqueia apenas o shadow read-only; não
abandona ordem aberta porque essa composição não possui rota financeira.

A prova IPC curta usa o Deriv fake em subprocesso, duas séries compartilhando o mesmo supervisor,
injeção de kill do worker, novo PID, restauração de duas subscriptions e telemetria do processo
filho via `PopenChildProcessProbe`. O teste permanece local e não usa Deriv real, conta demo,
segredo, `dispatch=True` ou `ORDER_SUBMIT`.

`BrokerShadowTemporalSoakRunner` adiciona uma janela monotônica controlada sobre o runner bounded.
O plano exige duração positiva, ciclos mínimos, ciclos máximos, frequência de amostragem e limite de
amostras retidas. A execução termina ao atingir a janela ou o teto de ciclos, captura o snapshot
final antes do shutdown e gera um relatório JSON-safe com outcome, reason code, critérios e
amostras resumidas de recursos, health e subscriptions. O relatório não serializa candles, payloads
brutos, saldos, credenciais ou comandos financeiros.

Critérios de aceitação são explícitos: duração alcançada, ciclos mínimos, limite de falhas de poll,
limite de recoveries e, por padrão, estado final não degradado. Se a janela não é alcançada antes
do teto de ciclos, o relatório falha com `BROKER_SHADOW_TEMPORAL_SOAK_DURATION_NOT_REACHED` e a
sessão é encerrada. Isso transforma o soak em evidência persistível de operação read-only, ainda sem
daemon permanente.

`BrokerShadowTemporalSoakMatrixRunner` executa uma matriz local sequencial e bounded desses
cenários. IDs são validados e únicos, a quantidade total possui teto explícito e todos os cenários
continuam sendo executados mesmo quando um deles falha, para preservar o relatório comparativo.
Cada resultado mantém o relatório temporal redigido correspondente; exceção inesperada produz
`BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_RAISED`, força shutdown do runner read-only e não
serializa a mensagem da exceção. Se esse shutdown também falhar, o resultado usa
`BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED` e a comparação continua. A matriz só
passa quando todos os cenários passam.

## CLI local, publicação atômica e retenção

`python -m apps.core.soak_cli` é o entrypoint explicitamente opt-in da matriz local. A execução é
recusada com `SOAK_CLI_OPT_IN_REQUIRED` e exit code `2` quando nem `--run-soak-matrix` nem
`DUALTRADE_RUN_SOAK_MATRIX=1` estão presentes. Duração, ciclos e quantidade retida são validados
contra limites fechados; argumento fora do intervalo retorna `SOAK_CLI_ARGUMENT_INVALID`.

Os perfis `fast`, `standard`, `extended` e `chaos` definem somente duração, ciclos e amostras dentro
dos tetos. `none`, `intermittent_crash`, `sleep_resume_gap` e `heavy_load` geram uma
`FaultSchedule` determinística. Backpressure é exercido na cadência alternativa, suspensão no
cenário de resume e perda simulada do worker no cenário de recovery. Cada injeção e recuperação é
sumarizada por cenário/ciclo/tipo/reason code, sem exception bruta. Não existe conta, rede,
estratégia comercial, `TradeIntent`, Outbox ou `dispatch=True` nessa composição.

O relatório usa `atomic_write_json()`: JSON UTF-8 ordenado, temporário único no mesmo diretório,
flush + `fsync` e publicação por `os.replace`. `ReportRetentionManager` examina somente arquivos
regulares `soak_matrix_*.json` do diretório configurado, rejeita symlink/scope mismatch e remove os
mais antigos até satisfazer simultaneamente contagem e bytes. Defaults: 10 relatórios e 20 MiB;
tetos do modelo: 100 relatórios e 1 GiB. Falha de escrita/retenção encerra o CLI com exit code `1`
e mensagem estável sem exception externa.
Antes da publicação, `SecretScanner` verifica o payload JSON em memória. Match ou falha do scanner
encerra fechado, sem criar relatório parcial.

## Warm-up, cursor e equivalência

O número mínimo vem do manifesto. Enquanto faltam candles duráveis, a série fica `WARMING_UP` e
nenhum candle é entregue. Quando a continuidade está completa, os candles são entregues em ordem e
o próprio contrato do replay define o primeiro ponto de decisão.

Não existe `candle.delivered` global. O cursor é o `WarmupCheckpoint` do contexto completo de cada
run. Assim:

- crash após persistir candle e antes do pipeline reentrega o candle;
- crash antes do commit de decisão restaura o checkpoint anterior;
- crash depois do commit começa no próximo candle;
- runs/replays diferentes podem processar o mesmo candle independentemente.

A prova de 500 candles compara o hash do shadow ao replay limpo. Kills reais antes/depois do commit
300, iniciados pelo scheduler e dispatcher, restauram respectivamente os checkpoints 299 e 300 e
terminam no mesmo hash.

## Observabilidade

Eventos estruturados incluem scheduling, início/fim/lote/retry/falha de backfill, gap,
backpressure, reconnect, clock não confiável, mudança de health e delivery/commit shadow. Eventos
de candle usam somente ID, série, fechamento e run/correlação quando aplicável; não registram o
candle completo por padrão.

Métricas incluem requests/retries/failures, candles/duplicatas, gaps e recuperações, backpressure,
reconnect, warm-up, deliveries/decisões shadow, ticks live, candles live, timeouts, atraso de
dispatch, restaurações de subscription e comparações/divergências live-versus-replay. Nenhum campo
contém segredo ou credencial de broker.

A proveniência completa permanece no candle durável e nos eventos operacionais. Ela não entra no
payload da decisão `CANDLE_ACCEPTED`; redelivery/transport lineage não altera o hash estratégico.

## Limitações deliberadas

- `tick()`, `poll_once()` e `run_cycle()` continuam caller-driven; o host fornece política bounded,
  o soak broker-level fornece hospedagem bounded e a camada temporal gera relatório por janela, mas
  ainda não existe daemon/serviço de longa duração;
- o soak local cobre 10.000 ticks determinísticos e dedupe limitada, mas não mede uma sessão de
  horas com jitter de rede e uso real de memória/CPU;
- já existe lifecycle broker-level compartilhado, telemetria do processo filho, CLI e relatório
  atômico/retido, porém ainda falta soak real de horas em ambiente com jitter de rede;
- não há calendário de exceções para mercados que legitimamente não formem candle;
- o runtime continua no processo Core sem budget de CPU/timeout;
- não existe estratégia comercial liberada nem validação de rentabilidade;
- Deriv externo continua opt-in e somente leitura; demo live exige flag e variável de opt-in, e
  conta/endpoint real são rejeitados antes do socket.

Próximo marco: executar os perfis prolongados em hosts Windows suportados e revisar os artefatos
comparáveis como parte do gate formal da Fase 0, mantendo transportes fake, `DECISION_ONLY` e
`dispatch=False`.
