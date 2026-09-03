# P07 Validation — Research Core

Data/hora local: 2026-09-02 21:33 BRT.

## Escopo

Implementação do núcleo `research` do Strategy Lab:

- dataset e cobertura;
- lookup horário de payout;
- liquidação fim-de-vela;
- replay incremental;
- triagem Polars;
- penalidade determinística de atraso;
- candidatos com hash estável;
- séries sintéticas para testes.

Nenhuma ordem foi enviada. Nenhuma conta de broker foi usada. Nenhuma chamada Supabase real foi
executada na suíte comum.

## Requisitos cobertos

| Requisito | Evidência |
| --- | --- |
| R-RES-1 | `ResearchDataset` calcula cobertura por grade de 60 s, recusa cobertura `< 0.95` e gaps `in_session` não resolvidos. CLI `strategy-lab research --coverage-report` imprime relatório e retorna erro quando algum asset é recusado. |
| R-RES-4 | `settle()` decide com `c_t` e `c_t1`; empate é perda; payout vem do bucket horário; payout com `samples=0` exclui operação. |
| R-RES-5 | `replay_candidate()` instancia os três primitivos do candidato, alimenta vela a vela, coleta sinal quando regime permite e trigger/confirm concordam, e só usa `t+1` após a decisão para liquidação. |
| R-RES-6 | `apply_delay_penalty()` aplica subtração direta e determinística de `0.005` ou `0.010` sobre `p_hat`, conforme decisão documentada. |
| R-RES-10 parcial | Séries sintéticas cobrem passeio aleatório, edge injetado p=0,60 e fixture ilegal de oráculo/lookahead com `p_hat > 0.95`. |

## Implementação

Arquivos principais:

- `tools/strategy_lab/research/dataset.py`
- `tools/strategy_lab/research/payout_lookup.py`
- `tools/strategy_lab/research/outcome.py`
- `tools/strategy_lab/research/vector_scan.py`
- `tools/strategy_lab/research/replay_simulator.py`
- `tools/strategy_lab/research/delay_penalty.py`
- `tools/strategy_lab/research/candidate.py`
- `tools/strategy_lab/research/synthetic.py`
- `tests/test_research_p07.py`

Dependências autorizadas adicionadas ao `pyproject.toml` e `requirements.lock`:

- `polars`
- `duckdb`
- `numpy`
- `pyarrow`

## Testes P07

`tests/test_research_p07.py` contém 13 testes:

- replay sem acesso futuro cobrindo os 14 primitivos do `REGISTRY`;
- empate como perda;
- payout `None` exclui trade;
- triagem vs replay em candidato controlado;
- caminho Polars real para `session_window + range_break + candle_rejection`;
- edge sintético p=0,60 recuperado entre 0,58 e 0,62;
- oráculo ilegal/lookahead detectado com `p_hat > 0.95`;
- cobertura com recusa por `<95%` e gap in-session;
- lookup por `hour_ts` e `samples`;
- CLI `research --coverage-report` com Parquet local aprovado com saída 0;
- CLI `research --coverage-report` recusa asset com cobertura < 95% com saída 1;
- `Candidate.hash()` e `Candidate.stable_hash()` com estabilidade em dicionários e compatibilidade `__hash__()`;
- penalidade determinística de atraso (-0,5 pp default, -1,0 pp custom, piso 0).

## Validação executada

```text
python -m pytest tests/test_research_p07.py -q
```

Resultado:

```text
13 passed
```

Suíte completa:

```text
python -m pytest
```

Resultado:

```text
270 passed, 3 skipped
```

Checks:

```text
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall packages tools
git diff --check
```

Resultado:

```text
Ruff check: passed
Ruff format --check: passed
mypy strict: passed
compileall: passed
git diff --check: passed
```

Os 3 skips continuam sendo os testes staging de P05, que exigem `SUPABASE_STAGING_DB_URL`.

## Limitações

O carregamento real via Supabase e Parquet foi implementado, mas a validação externa com Supabase
staging continua pendente porque `SUPABASE_STAGING_DB_URL` não está configurado no ambiente. A
triagem vetorizada Polars tem caminho real para o trio range/rejection e fallback conservador para
outros candidatos; aprovação continua proibida fora do replay incremental.
