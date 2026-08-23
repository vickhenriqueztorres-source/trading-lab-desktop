# Closed Candle Ingress e Replay Determinístico

**Projeto:** DualTrade Desktop  
**Estado:** fatia local/simulada recuperável; transporte Deriv fake ligado ao ingress persistente e
nenhum dispatch financeiro

## Autoridade e fronteiras

O Trading Core continua sendo a única autoridade financeira. `strategy_data.db` guarda somente
candles, evidência de decisão, provas de replay e checkpoints de estratégia. Ele possui conexão,
migrações e writer locais próprios e recusa o nome `state.db`. O replay usa um `RiskLedger` novo a
cada execução e uma fronteira de intenção sintética que não possui worker, banco financeiro ou
capacidade de dispatch.

```text
Deriv fake transport / FakeCandleSource
    → Deriv Worker subprocesso read-only
    → IPC v1 MarketHistoryBatch
    → DerivCandleHistoryPump limitado
    → DerivCandleAdapter validado
    → CandleEnvelope
    → ClosedCandle (inteiros escalados)
    → CandleIngress
    → SqliteCandleRepository (`strategy_data.db`)
    → CoreCandlePipeline
    → Strategy Runtime
    → Signal Arbiter
    → Portfolio Allocator
    → Risk Ledger de replay
    → PersistentDecisionJournal encadeado
    → ReplayRecord + WarmupCheckpoint
```

## Candle canônico

`ClosedCandle` é imutável e contém broker, símbolo, timeframe em segundos, tempos Unix em
milissegundos, OHLC em inteiros, `price_scale`, origem, ID da entrega e timestamps de origem e
recebimento. A conversão para `Decimal` ocorre somente na ponte do Core.

`candle_id` é SHA-256 canônico de broker + símbolo + timeframe + tempos + OHLC + escala. O
`source_event_id` não participa da identidade: uma redelivery idêntica recebe o mesmo ID.

O ingress possui resultados explícitos `ACCEPTED`, `DUPLICATE`, `OUT_OF_ORDER` e `INVALID`.
Candle aberto, schema externo inválido, gap, série fora de ordem ou store cheio falham fechado e
não alcançam Strategy Runtime. O repositório SQLite usa `candle_id` e uma constraint única por
stream/fechamento. Redelivery compatível retorna `ALREADY_EXISTS`; o mesmo fechamento com conteúdo
divergente retorna `CANDLE_CONFLICT` e nunca sobrescreve a evidência existente.

## Replay e tempo

`ReplayRequest` fixa estratégia/versão, contexto, hash do manifesto, hash da configuração,
entitlements, budgets com moeda e candles. O engine ordena por `close_time_ms + candle_id`, cria do
zero catálogo, Runtime, Arbiter, Allocator, Risk Ledger, ingress, clock e journal, ou reidrata uma
sessão validada a partir da evidência persistida.

`ReplayClock` avança somente para o fechamento do candle e não pode regredir. O engine não consulta
`datetime.now()`, `time.time()`, UUID aleatório ou rede. Manifesto e configuração divergentes são
rejeitados antes da execução. Suspensão é revalidada pelo catálogo em cada avaliação e bloqueia
novos intents sintéticos.

## Journal encadeado

Cada `DecisionRecord` preserva evento imutável, tempo lógico em milissegundos, hash do payload,
hash do evento anterior e hash do evento atual. O hash atual é:

```text
SHA256(previous_event_sha256 + canonical_event)
```

O journal é append-only na API, possui `UNIQUE(run_id, sequence)` e não descarta eventos
silenciosamente. `ReplayResult.final_hash` identifica a cadeia completa. Alterar um campo de evento
invalida a verificação. Payloads, manifests, configurações, resultados, estados e checkpoints usam
o mesmo `canonical_bytes`: JSON com chaves ordenadas, separadores compactos e UTF-8.

Durante o processamento, os eventos de um candle ficam somente no journal em memória. Ao terminar
a avaliação, `SqliteCandleDecisionCommitRepository` grava o lote completo e seu
`WarmupCheckpoint` na mesma transação SQLite. Falha antes do commit não publica nenhum dos dois;
sucesso publica ambos. Um checkpoint idêntico pode ser reutilizado por outro `run_id`, mas journal
e checkpoint incompatíveis falham fechado.

## Checkpoint e restart

`RuntimePhase` representa explicitamente `CREATED`, `WARMING_UP`, `READY`, `ACTIVE`, `SUSPENDED` e
`STOPPED`. O checkpoint não serializa o objeto Python: persiste somente `StrategyStateV1`, com
versão, IDs dos candles necessários, contador, hash do estado e hash canônico do checkpoint.

O restore valida, nesta ordem, hash do checkpoint, versão do estado, manifest, configuração,
contexto, fronteira do journal e presença/posição dos candles. Divergência gera reason code estável
e nenhuma avaliação nova. A prova automatizada fecha o banco após 300 candles, reabre o arquivo,
restaura o warm-up, reenvia deliberadamente o candle 300 e continua até 500. O resultado é idêntico
à execução limpa 1–500 em estado, sinais, arbitragem, alocação, risco e hash final do journal.

A prova de crash também executa o replay em subprocesso e o mata imediatamente antes e depois do
commit do candle 300. No primeiro caso, o restore parte do candle 299 e reprocessa o candle bruto já
persistido; no segundo, parte do checkpoint 300. A prova shadow adicional inicia a entrega pelo
scheduler monotônico, Market Health Gate e `AcceptedCandleDispatcher`. Ambos terminam iguais à
execução limpa até 500.

Um `ReplayRecord` append-only fixa manifest, configuração, primeiro/último candle, quantidade, hash
final do journal e hash do resultado. Reexecutar um run concluído reidrata a prova e não duplica
decisões; uma prova incompatível falha fechado.

## Adapter Deriv somente leitura

`DerivCandleAdapter` valida um schema externo estrito, allowlist de símbolo, fechamento confirmado,
timestamps, OHLC e precisão decimal textual antes de criar `ClosedCandle`. O adapter não importa
transporte, credenciais, estratégia, allocator, risco ou submissão. `DerivCandleHistoryPump` chama
somente o histórico read-only normalizado do worker, com lote máximo obrigatório e sem fila ou
retry próprio, e passa cada item por adapter → ingress. O batch preserva `message_id`,
`correlation_id` e `causation_id`; a primeira proveniência também fica no `source_event_id` do
candle persistido.

O contract test end-to-end usa subprocesso Deriv, fake transport, IPC v1 e `strategy_data.db`.
Redelivery e restart explícito do worker produzem `DUPLICATE`, não uma segunda gravação. Candle
parcial não entra; overflow é recusado antes/depois da resposta; gap e fora de ordem permanecem
resultados de qualidade explícitos.

## Limitações deliberadas

- o candle bruto é persistido antes da unidade de decisões; após crash pré-commit ele pode existir
  sem decisões, mas é reprocessado idempotentemente a partir do último checkpoint confirmado;
- a ligação atual possui scheduler determinístico acionado por `tick()`, mas ainda não existe loop
  contínuo supervisionado nem assinatura contínua de candles;
- somente candles duráveis e contínuos com health saudável chegam ao Strategy Runtime em
  `DECISION_ONLY`, sempre com `dispatch=False`;
- a intenção de replay é evidência sintética e não cria `TradeIntent`, `RiskReservation` ou Outbox
  no `state.db`; como não existe dispatch, nenhuma ordem externa pode ser produzida;
- o replay não modela fill, payout ou P&L e não constitui backtest de rentabilidade.

Próximo passo: executar Shadow Runtime contínuo com métricas de atraso e divergência live/replay,
soak test prolongado e `dispatch=False` preservado.
