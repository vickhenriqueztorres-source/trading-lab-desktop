# CAT-04 — Replay de referência equivalente à estratégia completa

Data: 2026-09-05.

Escopo executado: Strategy Lab `research/replay_simulator.py`, contrato público de replay e teste
independente no Desktop Bot. Nenhum Supabase remoto, broker, publicação, ordem, build ou credencial
foi usado.

## Resultado

CAT-04 foi implementado localmente.

O Strategy Lab agora diferencia a simulação histórica close-to-close da execução real do broker:
o replay decide no fechamento da vela `t` e só liquida quando existe a próxima vela completa do
mesmo timeframe. A próxima linha depois de um gap não é tratada como `t+1`.

O replay também respeita:

- `candidate.tf`;
- `candidate.hours`;
- warmup derivado das três primitivas;
- `SessionWindow` derivada de `hours_utc`;
- gate de composição F1 por `adx_max`;
- gate de composição F4 por `width_ratio_max`;
- volume ausente como bloqueio quando a família depende de `tick_volume_ratio`.

Nenhuma estratégia foi aprovada ou publicada nesta fase.

## Contrato público criado

Arquivo:

`strategy-lab/contracts/replay_contract_vectors.v1.json`

SHA-256 canônico:

`1059d58db4ead251e9be720218427b5dcb883437142d1af76a07ce43782acdde`

O arquivo contém 7 casos e é consumido pelo bot sem importar código do Strategy Lab.

| Caso | Família | Ativo | TF | Traces | Trades | Cobertura principal |
|---|---:|---|---:|---:|---:|---|
| `f1_eurusd_m1_gate_boundary` | F1 | EURUSD | M1 | 47 | 0 | warmup, confirmação, gate ADX |
| `f2_eurusdotc_m5` | F2 | EURUSD-OTC | M5 | 33 | 0 | timeframe da receita |
| `f3_gbpusd_m15` | F3 | GBPUSD | M15 | 1 | 1 | liquidação no próximo bucket M15 |
| `f4_usdjpy_missing_volume` | F4 | USDJPY | M1 | 47 | 0 | `TICK_VOLUME_UNAVAILABLE` |
| `f5_eurjpy_outside_hours` | F5 | EURJPY | M1 | 29 | 0 | `OUTSIDE_HOURS` |
| `f3_tie_is_loss` | F3 | AUDUSD-OTC | M1 | 1 | 1 | empate é perda |
| `f3_settlement_gap` | F3 | USDCHF-OTC | M1 | 1 | 0 | próxima linha após gap não liquida |

## Validação executada

Strategy Lab:

- `pytest tests/test_replay_contract_v1.py tests/test_research_p07.py tests/test_research_runner.py tests/test_cat03_dataset_contract.py -q`
  - Resultado: 27 passed.
- `ruff check .`
  - Resultado: aprovado.
- `ruff format --check .`
  - Resultado: aprovado.
- `mypy`
  - Resultado: aprovado, 82 source files.

Desktop Bot:

- `pytest tests/contract/test_replay_contract_vectors_v1.py -q`
  - Resultado: 7 passed.
- `ruff check tests/contract/test_replay_contract_vectors_v1.py`
  - Resultado: aprovado.
- `ruff format --check tests/contract/test_replay_contract_vectors_v1.py`
  - Resultado: aprovado.
- `mypy tests/contract/test_replay_contract_vectors_v1.py`
  - Resultado: aprovado.

Observação Windows: o `pytest` do Strategy Lab voltou a emitir, após exit code 0, o aviso de limpeza
do diretório temporário `pytest-current` por `PermissionError`. O aviso não alterou o resultado dos
testes.

## Fora do escopo

- Supabase remoto/staging.
- Deploy de migration.
- Coleta real.
- Publicação de manifesto.
- Execução financeira.
- Build do EXE.
- Aprovação de novas estratégias.
