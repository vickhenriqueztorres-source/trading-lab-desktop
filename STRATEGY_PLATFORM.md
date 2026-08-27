# Strategy Platform — Fase 0 Simulada

**Projeto:** DualTrade Desktop  
**Status:** contratos executáveis locais com ingresso fechado e replay determinístico; nenhuma
estratégia é apresentada como lucrativa

## 1. Autoridade e ordem obrigatória

```text
MarketCandle fechado e validado
→ Strategy Runtime
→ Signal Arbiter
→ Portfolio Allocator
→ Risk Ledger
→ TradeIntent + RiskReservation + Outbox
→ worker simulado
```

Estratégias geram somente direção e evidência. Elas não recebem credencial, saldo ou API de broker,
não escolhem stake, não reservam risco e não persistem estado financeiro. O Trading Core continua
sendo a única autoridade financeira local.

## 2. Manifest v1 e catálogo

Cada versão imutável declara:

```text
strategy_id, version, code_hash, strategy_pack,
supported_brokers[], supported_products[], supported_timeframes[],
required_data[], warmup_candles, parameter_schema,
risk_class, validation_report_id, release_status
```

O catálogo aceita somente implementação empacotada localmente. SHA-256 é calculado sobre o artefato
declarado pela própria implementação e comparado ao manifesto. A Fase 3 não baixa nem executa
Python remoto, plugins, `eval` ou pacotes externos.

Lifecycle suportado:

```text
DRAFT → BACKTESTED → WALK_FORWARD_VALIDATED
→ REPLAY_VALIDATED → PRACTICE_VALIDATED → RELEASED
→ SUSPENDED / RETIRED
```

Promoções fora de ordem falham. `RELEASED` exige relatórios/evidências aprovadas de todos os estágios
(`BACKTEST`, `WALK_FORWARD`, `REPLAY`, `PRACTICE`) no `SqliteValidationRepository` (`strategy_data.db`)
com `code_hash` idêntico ao artefato empacotado.

### 2.1 Validação Estatística e Repositório Durável (Fase 3 — Fatia 3.2)

- **`SqliteValidationRepository`**: Persistência durável de `ValidationReport` na tabela `strategy_validation_reports` em `strategy_data.db` (isolado do `state.db` financeiro).
- **`StrategyPerformanceMetrics`**: Cálculo determinístico com `Decimal` de Win Rate, Profit Factor, Expectancy, Max Drawdown absoluto (minor units) e relativo (%), Duração média e distribuição por regime de mercado.
- **`WalkForwardEngine`**: Particionamento cronológico em janelas *In-Sample* e *Out-of-Sample* deslizantes, garantindo zero sobreposição temporal e zero lookahead bias.
- **Enforcement do Gate de Promoção**: `StrategyCatalog.promote_strategy` verifica estritamente os pré-requisitos de validação de cada estágio antes de permitir avanço de status.

## 3. Runtime isolado

A chave de isolamento contém:

```text
strategy_id + version + broker + account + product
+ symbol + timeframe + configuration_version + parameters
```

O runtime:

- aceita apenas candle fechado com broker, símbolo e timeframe compatíveis;
- rejeita duplicidade e candle fora de ordem;
- respeita warm-up;
- limita o número total de instâncias;
- usa ID de sinal determinístico para replay/idempotência;
- revalida catálogo, status, compatibilidade e entitlement em cada avaliação;
- perde apenas buffers efêmeros após crash, exigindo novo warm-up.

`CoreCandlePipeline` é a fronteira anterior ao runtime. Somente um `ClosedCandle` validado,
deduplicado e ordenado em `CandleIngress` é convertido de inteiros escalados para `Decimal` e
entregue ao runtime. Candle aberto, gap, duplicidade e fora de ordem não alcançam a estratégia.

Uma `configuration_version` não pode reaparecer com parâmetros diferentes dentro do mesmo manager.

## 4. Signal Arbiter

Arbitragem ocorre por broker + conta + produto + símbolo + timeframe.

| Entrada | Decisão MVP |
|---|---|
| um sinal elegível | encaminhar |
| sinais iguais | uma única intenção lógica; stake não é somada |
| sinais opostos | nenhuma entrada |
| sinal expirado | rejeitar |
| estratégia suspensa/retirada | rejeitar |
| contextos diferentes | arbitrar separadamente |

O Arbiter reconsulta o status do catálogo para impedir que um sinal emitido antes de uma suspensão
abra nova entrada depois dela. A auditoria em memória possui capacidade limitada.

## 5. Portfolio Allocator

O allocator recebe stake solicitada pela configuração do Core e snapshots imutáveis de orçamento:

- restante por estratégia;
- restante da conta;
- restante global;
- moeda explícita.

Ele é uma função pura: aprova exatamente a stake solicitada ou bloqueia. Não reduz stake
silenciosamente, não soma sinais coincidentes e não substitui o Risk Ledger. Moedas diferentes ou
orçamento insuficiente falham fechado.

## 6. Suspensão e ordens existentes

`SUSPENDED` e `RETIRED` bloqueiam somente novos sinais/entradas. A suspensão não mata worker, não
libera reserva e não interfere no processador de eventos ou na reconciliação. Testes integram uma
ordem já aceita, suspendem a estratégia, bloqueiam a próxima entrada e ainda aplicam sua liquidação.

## 7. Limitações atuais

- catálogo e Validation Registry ainda são reconstruídos em memória;
- não há migração para decisões de arbitragem/validação duráveis no `state.db`;
- `strategy_data.db`, separado do banco financeiro, persiste candles, journal append-only, provas de
  replay e checkpoints `StrategyStateV1` com hashes canônicos;
- `ReplayEngine` restaura Runtime/warm-up e journal após reabertura, mas o Risk Ledger permanece
  sintético e exclusivo da execução, sem `TradeIntent`, Outbox ou dispatch;
- decisões e checkpoint de cada candle são confirmados em uma única transação; kills reais antes e
  depois do commit 300 restauram e terminam iguais ao replay limpo até 500;
- o transporte Deriv fake em subprocesso entrega histórico fechado pelo IPC ao adapter e ao
  ingress persistente, com lote limitado e IDs de correlação preservados;
- scheduler monotônico, planner paginado com overlap e Market Health Gate por série agora protegem
  a entrega; o dispatcher shadow exige candle durável/contínuo e passa `dispatch=False`;
- histórico e stream live convergem no mesmo ingress; assinatura só é restaurada após backfill da
  geração corrente, e divergência de hash/sinais/decisões contra replay bloqueia Market Health;
- o runtime contínuo é poll-driven e tem prova determinística de 400 candles históricos + 100 live;
  o Core agora possui soak broker-level bounded, mas ainda não possui daemon de longa duração nem
  budget isolado de CPU por estratégia;
- a composição Core/IPC possui lifecycle explícito e prova de kill/restart do worker: o novo PID
  somente restaura subscription depois do overlap da geração corrente;
- o host shadow limita polls/recoveries por ciclo, aplica fairness/circuit breaker por série e
  encerra delivery se budgets de CPU/RSS/lag forem excedidos;
- `SharedMarketTickRouter` permite que múltiplos runtimes shadow consumam uma única sessão live
  Deriv read-only sem competir pela fila IPC; o roteamento é bounded por série e falha fechado em
  backpressure ou escopo desconhecido;
- `BrokerShadowSession` compartilha um único supervisor/cliente Deriv read-only, faz polling justo
  entre séries e restaura todas as subscriptions com um único restart explícito do worker;
- `BrokerShadowSoakRunner` executa ciclos finitos sobre a sessão broker-level, agrega telemetria do
  Core/subprocesso e falha fechado em estouro de budget/recovery sem criar rota financeira;
- `BrokerShadowTemporalSoakRunner` transforma esses ciclos em uma janela monotônica com amostras
  bounded, critérios de aceitação e relatório JSON-safe sem candle bruto ou credencial;
- `BrokerShadowTemporalSoakMatrixRunner` compara cenários locais bounded com cadências e falhas
  programadas, continua após falha para preservar evidência e só passa quando todos passam;
- o CLI de soak exige opt-in, executa a matriz local em `DECISION_ONLY` e publica relatório atômico
  com retenção bounded; ele não recebe conta, estratégia comercial ou comando financeiro;
- o modo default é `DECISION_ONLY`; a capability Deriv read-only rejeita qualquer tentativa de
  elevar a composição para dispatch;
- não existe estratégia comercial `RELEASED`; implementações dos testes usam evidência sintética;
- execução do código de estratégia ainda ocorre no processo Core, sem orçamento de CPU/timeout;
- Portfolio Allocator usa snapshot fornecido pelo Core; concorrência financeira continua sendo
  responsabilidade final do Risk Ledger e das constraints duráveis;
- assinatura de pacotes remotos pertence a uma fase futura; FR-109 não foi implementado.

Detalhes e limites do contrato estão em `docs/CLOSED_CANDLE_REPLAY.md`.

## 8. Gestão de risco especializada para DIGITDIFF

O Core mantém uma `DigitRiskConfig` imutável para operações Deriv `DIGITDIFF`. Stake, Stop Loss,
Take Profit e P&L usam minor units inteiros com moeda USD explícita. A configuração também limita
perdas consecutivas, define cooldown monotônico e restringe o ativo a uma allowlist de índices
sintéticos.

Antes da reserva e do despacho, o `RiskLedger` verifica, em modo fail-closed:

1. Stop Loss diário (`HG_DAILY_STOP_REACHED`);
2. Take Profit diário (`HG_DAILY_TAKE_PROFIT_REACHED`);
3. cooldown pós-perda (`HG_COOLDOWN_ACTIVE`).

As travas impedem somente novas entradas. Eventos e liquidações de contratos já abertos continuam
sendo processados e persistidos. A UI não grava configuração financeira: envia
`UI_UPDATE_DIGIT_RISK_CONFIG_COMMAND` pelo IPC autenticado e recebe a configuração efetiva no
snapshot seguinte.

O campo chamado “confiança quântica” é apenas um limiar decimal configurável consumido por uma
estratégia futura. Ele não representa probabilidade calibrada, previsão de lucro ou garantia de
resultado, e esta fatia não introduz uma estratégia comercial nem afirma vantagem estatística.

## 9. Motor de ticks e execução DIGITDIFF de 1 tick

O Deriv Worker mantém uma janela circular fixa do índice sintético selecionado. Cada tick preserva
o preço em `Decimal`, extrai o último dígito sem conversão para `float` e atualiza em O(1) as dez
frequências e a matriz 10x10 de transições, inclusive ao ejetar o item mais antigo. A memória não
cresce com o tempo. Latência de recebimento e custo local usam relógio monotônico.

A projeção da UI exibe dez barras verticais: maior frequência observada em âmbar e menor em ciano.
Esses contadores descrevem somente a janela histórica em memória. Não são probabilidade calibrada,
sinal, previsão nem garantia de retorno.

Uma intenção `DIGITDIFF` válida continua seguindo a ordem financeira obrigatória:

```text
Risk Ledger → TradeIntent + reserva + Outbox no SQLite → dispatch → buy Deriv
```

O comando durável inclui `prediction_digit`. Somente depois do commit o worker envia a compra direta
oficial com stake em USD, `DIGITDIFF`, barreira, duração de um tick e IDs de rastreio. A liquidação
usa o `profit` oficial e confere o último dígito de saída contra a barreira; conflito ou ausência de
evidência permanece fail-closed. Contratos não-DIGITDIFF continuam usando proposta seguida de compra
por ID.

## 10. Três estratégias de dígitos em validação Demo

A aba Deriv acompanha três hipóteses determinísticas. Em dados públicos e conta Real elas permanecem
`RESEARCH_SHADOW`. Na conta Demo explicitamente conectada, o botão **Ligar Bot** promove somente o
sinal novo daquela sessão a `PRACTICE_VALIDATION`, passando por alocação, Risk Ledger, commit de
`TradeIntent`/reserva/Outbox e worker autenticado:

1. **Tail Probability Edge** — avalia `DIGITOVER` e `DIGITUNDER` nas barreiras 2/3/4 e 7/6/5,
   condicionado à paridade do dígito anterior.
2. **Selective Differs Edge** — escolhe o dígito menos provável para `DIGITDIFF`, mas exige que as
   três janelas independentes indiquem o mesmo dígito e superem o piso conservador.
3. **Parity Regime Edge** — avalia `DIGITEVEN` e `DIGITODD` condicionado à paridade anterior.

Todas usam os últimos 500 ticks, janelas de 200/350/500 observações e limite Wilson unilateral de
99%. Um sinal só aparece quando as três janelas concordam; dados uniformes de controle permanecem em
`MONITORING`. O hot path mede latência monotônica e a projeta para a UI. O executor Demo consome no
máximo um candidato novo por epoch, escolhe deterministicamente a maior margem conservadora quando
há simultaneidade e nunca reutiliza sinal vencido. Real não possui capability de submissão. A gestão
de stake (fixa ou Bounded Martingale com teto de etapas e stop loss) é de domínio exclusivo do
Portfolio Allocator e Risk Ledger do Core; estratégias não calculam stake e martingale ilimitado é
proibido.

### Radar multiativo Shadow

O Core observa `R_10`, `R_25`, `R_50`, `R_75` e `R_100` em paralelo, desde que a descoberta
`active_symbols` confirme que o ativo está negociável e `contracts_for` confirme ao menos um
contrato de dígitos suportado. Cada ativo possui sua própria instância de
`DerivDigitShadowEngine`, janela de 500 ticks, aquecimento, decisões e latência; históricos e
transições nunca são compartilhados entre símbolos.

O radar ordena somente candidatos estatísticos atuais pela margem conservadora entre a estimativa
e o piso da estratégia. No máximo um item recebe a marca visual de candidato principal. Quando
nenhum ativo supera o filtro, o estado é explicitamente **sem vantagem/abstenção**. Esse ranking:

- é somente `RESEARCH_SHADOW` e não cria `TradeIntent`, reserva ou ordem;
- não muda automaticamente o ativo escolhido pelo operador;
- não participa do cálculo de stake ou da sequência de Martingale;
- não representa EV líquido, porque a camada de payout/custos ainda não foi incorporada;
- não pode derrubar a conexão do ativo principal se um stream secundário falhar.

Uma promoção futura para seleção executável exige payout válido e recente por ativo/contrato,
estimativa calibrada fora da amostra, EV líquido conservador, histerese/cooldown, limites de
exposição e aprovação explícita no Core. Até lá, a UI apresenta o radar como evidência de pesquisa.
