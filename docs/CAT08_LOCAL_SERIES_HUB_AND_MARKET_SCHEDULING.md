# CAT-08 — Hub local de séries e agendamento de mercado

Data: 2026-09-05.

Produto alterado: BOT. Nenhum Supabase remoto, corretora, ordem financeira ou build do EXE foi
executado nesta etapa.

## Objetivo

Buscar cada série de candles da IQ Option uma vez por fechamento relevante, sem multiplicar
requisições por estratégia, preservando os guards do worker, o orçamento de mensagens e a
separação absoluta entre dados de mercado e submissão financeira.

## Implementado

- Criado `apps/core/iqoption_series_hub.py`, componente local e data-only para gerenciar séries
  de candles da IQ Option.
- A chave da série inclui `broker`, `account_id`, `product`, `generation`, ativo exato e timeframe.
  `EURUSD` e `EURUSD-OTC` permanecem séries diferentes, sem normalização por símbolo-base.
- O `IqOptionAutoTrader` passou a consumir o hub para candles, mantendo o worker/client atual e a
  rota financeira existente pelo Core.
- O cache antigo por `(symbol, timeframe, epoch)` foi removido do trader. O hub deduplica por
  `(series_key, close_epoch)`, então várias receitas na mesma série não geram várias buscas.
- Reconnect/session replacement troca o `generation` da chave. Quando o worker/client muda, a série
  anterior não contamina a nova sessão.
- O limite silencioso `min(120, warmup + 3)` foi removido. O hub solicita `warmup + 3` até a
  capacidade explícita `IQOPTION_SERIES_MAX_FETCH_COUNT = 1000`; acima disso rejeita previamente
  com `WARMUP_CAPACITY_EXCEEDED`. O RSI local demo preserva seu bootstrap legado de 20 candles sem
  mudar o contrato de manifesto.
- O orçamento local de mercado continua governado por `IQOptionMessageBudget`: 60 mensagens/min para
  market data, dentro do teto interno total documentado de 90/min. O alvo estável documentado para
  16 ativos × 3 TFs é `20.2667` fetches/min, sem consumir reserva financeira/recovery.
- O hub valida candles antes de publicar snapshot: somente candles fechados, broker IQ Option,
  ativo exato e timeframe exato. Também registra parcial rejeitado, incompatível, duplicado,
  batch fora de ordem, gap e correção histórica.
- Criado scheduler puro de requisições com prioridade `RECOVERY > BOOTSTRAP > STEADY`, deadline de
  fechamento e controle de fila limitada.
- O hub não possui API financeira (`buy`, `submit_order`, etc.) e os testes verificam essa fronteira.
- Durante a regressão, também foram preservados três contratos próximos:
  - RSI local demo continua capaz de rodar em testes antigos sem probe de payout, mas quando o
    client expõe `iqoption_binary_payout` a verificação continua fail-closed;
  - cache local de manifesto inválido/sem assinatura no profile bloqueia fallback para manifesto
    embarcado, impedindo que um cache hostil seja mascarado;
  - o painel agregado volta a expor os cards locais Deriv sem converter essas entradas em receitas
    IQ Option nem conceder autoridade financeira.

## Testes adicionados

- `test_same_series_same_close_deduplicates_fetch_for_many_recipes`.
- `test_exact_asset_key_keeps_otc_and_spot_distinct`.
- `test_reconnect_generation_invalidates_old_series_snapshot`.
- `test_partial_incompatible_duplicate_out_of_order_gap_and_correction_are_recorded`.
- `test_historical_correction_on_next_fetch_replaces_snapshot`.
- `test_warmup_above_capacity_rejects_explicitly_without_silent_120_cut`.
- `test_message_budget_fail_closed_without_fetch`.
- `test_full_queue_is_reported_without_fetch`.
- `test_scheduler_fairness_for_16_assets_and_3_timeframes`.
- `test_series_hub_does_not_expose_financial_submission_api`.

## Validação executada

- Testes focados executados:
  `python -m pytest tests/unit/test_iqoption_series_hub.py tests/unit/test_iqoption_auto_trader.py -q`
- Resultado inicial: **22 passed**.
- Regressões focadas após ajustes:
  - IQ Auto/Candidates/Recovery/e2e RSI: **67 passed**.
  - `tests/integration/test_iqoption_connection_projection.py`: **10 passed**.
  - `tests/integration/test_launcher_process_tree.py`: **9 passed**.
- Suíte completa do BOT: **1266 passed, 4 skipped**.
- `ruff format --check apps packages tests`: aprovado.
- `ruff check apps packages tests`: aprovado.
- `mypy apps packages`: aprovado em 305 arquivos.
- `compileall apps packages`: aprovado.
- `git diff --check`: aprovado.

Observação Windows: o pytest emitiu aviso de cleanup de `pytest-current` após retorno de sucesso.
O processo retornou exit code 0 na rodada final.

## Não executado

- Supabase remoto/staging.
- Coleta real.
- Login ou conexão IQ Option real/demo.
- Ordem financeira.
- Build de EXE.
- Benchmark visual/EXE.

## Veredito

CAT-08 implementado localmente no BOT. A alimentação de candles da IQ Option agora é compartilhada,
fenceada por geração e limitada por orçamento/capacidade explícitos, sem habilitar nova execução
financeira.
