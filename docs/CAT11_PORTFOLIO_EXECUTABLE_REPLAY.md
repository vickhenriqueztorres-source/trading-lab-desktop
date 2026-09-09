# CAT-11 — Replay da frequência realmente executável do portfólio

Data: 2026-09-05.

Produto alterado: `strategy-lab/`, módulo `tools/strategy_lab/research/`.

Escopo: diagnóstico/simulação. Nenhuma corretora foi acessada, nenhuma ordem foi enviada e nenhum
backend remoto foi usado.

## Objetivo

Medir a frequência que o bot do cliente realmente conseguiria executar a partir de um catálogo de
receitas, separando:

- sinais brutos;
- oportunidades conflitantes ou duplicadas;
- oportunidades bloqueadas por uma ordem em voo;
- bloqueios por payout, warmup, horário, stale, risco, prazo, reconnect, candle atrasado ou falha
  de execução simulada.

Isso impede a leitura enganosa de “30 estratégias = 30 vezes mais operações”. Estratégias que
disparam no mesmo evento podem aumentar o número de sinais, mas não aumentam necessariamente a
amostra executável.

## Implementação

Arquivo novo:

- `strategy-lab/tools/strategy_lab/research/portfolio_replay.py`

APIs principais:

- `PortfolioOpportunity`: uma oportunidade já derivada do replay de decisão.
- `PortfolioReplayConfig`: hipóteses explícitas de snapshot, seed, capital, stake, cooldown,
  latência, TTL, limites e período de cobertura.
- `run_portfolio_replay(...)`: aplica arbitragem, prazo, payout, risco, cooldown e uma ordem em voo.
- `compare_portfolio_sizes(...)`: compara prefixos determinísticos de 10/20/30/50 receitas.
- `opportunities_from_replay_contract(...)`: consome o artefato público
  `strategy-lab/contracts/replay_contract_vectors.v1.json`, sem importar código do bot.
- `portfolio_result_to_markdown(...)`: relatório humano reprodutível por snapshot + seed.

O módulo é data-only:

- sem import do Desktop Bot;
- sem IQ Option;
- sem Deriv;
- sem Supabase remoto;
- sem `buy`;
- sem preenchimento inventado para ordem recusada.

## Contrato de execução simulado

1. A decisão vem de vela fechada já simulada pelo replay individual.
2. A oportunidade só pode executar dentro do TTL configurado.
3. Payout ausente bloqueia a operação.
4. Direções opostas no mesmo ativo/timeframe/fechamento viram `CONFLICT`.
5. Receitas duplicadas no mesmo ativo/timeframe/direção/fechamento não multiplicam amostra.
6. Apenas uma ordem pode ficar em voo por conta.
7. Perda debita stake cheio; vitória credita `stake * payout_return_ratio`.
8. Empate já chega como perda a partir do contrato de replay individual.
9. Falhas simuladas permanecem marcadas como bloqueio; o replay nunca assume execução externa.

## Evidência numérica

Casos novos em `strategy-lab/tests/test_cat11_portfolio_replay.py`:

| Prova | Resultado |
|---|---:|
| 4 sinais em janela de 10 min, com sobreposição | 4 sinais brutos / 2 executáveis |
| Taxa correspondente | 576 sinais/dia / 288 operações executáveis/dia |
| 2 sinais opostos no mesmo contexto | 0 executáveis / 2 `CONFLICT` |
| 3 sinais duplicados no mesmo contexto | 1 executável / 2 `CONFLICT` |
| Vetor público CAT-04 convertido em portfólio | 2 oportunidades / 1 executável / 1 `ORDER_IN_FLIGHT` |
| Biblioteca com 50 receitas duplicando o mesmo evento | 50 sinais / 1 executável / 49 `CONFLICT` |

Motivos cobertos em teste:

- `ELIGIBLE`;
- `CONFLICT`;
- `ORDER_IN_FLIGHT`;
- `MISSING_PAYOUT`;
- `OUTSIDE_HOURS`;
- `WARMUP`;
- `STALE`;
- `RISK`;
- `DEADLINE_EXPIRED`;
- `EXECUTION`;
- `RECONNECTING`;
- `CANDLE_DELAYED`;
- `INVALID_DIRECTION`.

## Limitações

- O CAT-11 não seleciona portfólio; isso fica para o CAT-12.
- O CAT-11 não mede CPU/memória do EXE; benchmark no cliente fica para CAT-19.
- O CAT-11 não publica manifesto.
- O CAT-11 não usa Supabase remoto/staging.
- O CAT-11 não prova execução externa na IQ Option; ele mede apenas a frequência executável sob
  contrato público e hipóteses declaradas.

## Validação executada

Ambiente usado: `.venv` próprio do `strategy-lab/`.

- `python -m pytest tests/test_cat11_portfolio_replay.py -q`: **8 passed**.
- Regressão research/contrato:
  `python -m pytest tests/test_replay_contract_v1.py tests/test_research_p07.py tests/test_research_runner.py tests/test_cat07_executor_compatible_grammar.py tests/test_cat11_portfolio_replay.py -q`:
  **33 passed**.
- Suíte completa do Strategy Lab: **365 passed, 3 skipped**.
- `ruff check` focado: aprovado.
- `ruff format --check` focado: aprovado.
- `mypy` focado: aprovado.
- `ruff check .`, `ruff format --check .`, `mypy`, `compileall packages tools` e
  `git diff --check`: aprovados.

Observação: a regressão parcial emitiu um aviso pós-teste de cleanup do `pytest-current` no
diretório temporário do Windows (`PermissionError [WinError 5]`). O processo terminou com código 0
e os testes ficaram verdes.
