# CAT-09 — Grafo incremental de indicadores compartilhados

Data: 2026-09-05.

## Resultado

CAT-09 foi implementado localmente no BOT como infraestrutura de execução incremental em modo
shadow. A mudança cria somente a camada `series -> indicator`; família, arbitragem, risco e
submissão financeira continuam no caminho existente.

Nenhuma ordem foi enviada e nenhuma corretora foi acessada.

## Implementação

- Criado `apps/core/indicator_cache.py`.
- O cache usa a identidade exata da série do CAT-08:
  `broker`, `account_id`, `product`, `generation`, `asset` e `timeframe_seconds`.
- A chave do indicador também inclui:
  `name`, parâmetros canônicos, `primitives_version`,
  `execution_semantics_version`, `bootstrap_identity` e identidade auxiliar.
- `EURUSD` e `EURUSD-OTC` continuam sendo séries diferentes.
- RSI14 de contas, gerações, ativos, timeframes, versões ou parâmetros diferentes não compartilha
  estado.
- Cada candle fechado novo é processado no máximo uma vez por nó.
- Outputs por epoch são imutáveis e retêm:
  estado, motivo, direção, valor, meta, quantidade de candles vistos e warmup requerido.
- Reference counting permite múltiplas receitas compartilharem o mesmo nó e liberar uma receita
  sem destruir o estado das demais.
- Memória/backlog são limitados por `max_nodes` e `max_outputs_per_node`.
- Warmup valida quantidade, continuidade, timeframe, origem, candles fechados, correção histórica
  e volume quando o indicador exige.
- Estados expostos:
  `WARMING_UP`, `READY`, `INVALID`.
- Motivos principais:
  `WARMUP_INCOMPLETE`, `SERIES_MISMATCH`, `PARTIAL_CANDLE`, `SERIES_GAP`,
  `HISTORICAL_CORRECTION`, `TICK_VOLUME_UNAVAILABLE`, `SHADOW_MISMATCH`.

## Integração shadow

O `IqOptionAutoTrader` agora possui o cache incremental e executa comparação shadow para o RSI
local explícito `iqoption-rsi-demo`.

Essa integração:

- não altera a decisão financeira;
- não cria submit, buy, retry ou fallback financeiro;
- emite evento `iqoption_indicator_shadow_mismatch` com motivo estável quando houver divergência;
- invalida cache de indicadores junto com a invalidação de manifesto/série.

O componente de cache invalida o nó em caso de discordância; porém o `IqOptionAutoTrader` não usa
essa invalidação para alterar o caminho financeiro nesta etapa. As famílias completas e a
substituição segura do caminho legado ficam para CAT-10.

## Evidência de teste

Testes adicionados:

- `tests/unit/test_indicator_cache.py`

Cobertura lógica dos testes:

- cinco receitas RSI14 compartilham um único nó;
- parâmetros diferentes criam nós isolados;
- soltar uma receita mantém o nó vivo para as restantes;
- geração/reconnect usa nó distinto e pode invalidar sem vazamento;
- warmup incompleto, gap, símbolo divergente, candle parcial e correção histórica falham fechado;
- shadow compara com a mesma semântica;
- primeiro RSI do bootstrap é bit-idêntico à referência `calculate_wilder_rsi`;
- cache RSI bate com outputs de confirmação dos vetores públicos CAT-04;
- shadow não expõe API financeira nem altera submissão;
- limite de capacidade bloqueia novos nós.

Validação executada:

```text
python -m pytest tests/unit/test_indicator_cache.py tests/unit/test_iqoption_auto_trader.py -q
21 passed

python -m pytest -q
1275 passed, 4 skipped
```

Checks executados:

```text
python -m ruff check apps packages tests
All checks passed

python -m ruff format --check apps packages tests
503 files already formatted

python -m mypy apps packages
Success: no issues found in 306 source files

python -m compileall apps packages
OK

git diff --check
OK
```

Observação: `python -m ruff check .` não é o escopo canônico desta etapa e permanece bloqueado por
arquivos legados fora de `apps packages tests`, principalmente `docs/##  Arquitetura.py`, que contém
texto Markdown com extensão `.py`, além de ajustes antigos em `scripts/scrub_secrets.py`.

## Limites desta etapa

- Não foi feita troca do engine financeiro para consumir outputs incrementais.
- Não foi executado benchmark no EXE.
- Não foi gerado build.
- Não houve Supabase remoto, broker, login ou ordem.

## Próximo passo

CAT-10 deve integrar os outputs pelo `resolve_candidates`, admission/ticket, gates de manifesto,
payout fresco, LiveMonitor/SPRT, Risk Ledger, generation fencing e checagem final sob lock.
