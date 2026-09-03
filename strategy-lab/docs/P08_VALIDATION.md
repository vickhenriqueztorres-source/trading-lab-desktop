# P08 Validation — Statistical Gates & Coin Flip Test

Data/hora local: 2026-09-02 22:05 BRT.

## Escopo

Implementação e validação dos portões estatísticos sequenciais e critério formal de aprovação do Strategy Lab (`tools/strategy_lab/research/gates/`):

- Wilson score interval lower bound (95%, Decimal, z=1.959964);
- Walk-forward ancorado (treino 6 meses / teste 2 meses);
- Portão de estabilidade intertemporal (nenhuma janela $< p_{min}$, $\sigma < 3\text{ pp}$);
- Controle de múltiplas hipóteses (Benjamini-Hochberg FDR 5% sobre $N$ total) e permutação 1.000× ($P_{99}$);
- Portão de vizinhança paramétrica ($\pm 15\%$ na grade, mediana $\ge p_{min} + 1,5\text{ pp}$);
- PBO via CSCV com 16 blocos ($\binom{16}{8} = 12.870$ partições, $\text{PBO} < 20\%$);
- Pipeline sequencial com curto-circuito fail-closed;
- Critério formal de aprovação `approve_candidate` (todos os portões + $n \ge 500$ + Wilson pessimista $\ge p_{min} + 1,5\text{ pp}$).

---

## Requisitos Cobertos

| Requisito | Evidência |
| --- | --- |
| **R-RES-7** | Portões em ordem estrita: walk-forward ancorado $\to$ estabilidade ($\sigma < 3\text{ pp}$, min $\ge p_{min}$) $\to$ FDR 5% (BH sobre $N$) + permutação 1.000× ($P_{99}$) $\to$ vizinhança $\pm 15\%$ (mediana $\ge p_{min} + 1,5\text{ pp}$) $\to$ PBO/CSCV 16 blocos $< 20\%$. |
| **R-RES-8** | Critério formal de aprovação: Wilson inferior 95% (após penalidade pessimista de $-1,0\text{ pp}$) $\ge p_{min} + 1,5\text{ pp}$ com $n \ge 500$ operações fora da amostra, em todos os portões. |
| **R-RES-10** | Teste da moeda em CI (`test_coin_flip_approves_zero`): 2.000 candidatos aleatórios sobre passeio aleatório com 3 seeds fixos $\to$ exatamente 0 aprovados. |

---

## Estrutura de Arquivos

```text
tools/strategy_lab/research/gates/
├── __init__.py
├── approve.py
├── multiple_testing.py
├── neighborhood.py
├── pbo.py
├── pipeline.py
├── walk_forward.py
└── wilson.py
tools/strategy_lab/research/README.md
research/README.md
tests/test_gates_p08.py
```

---

## Resultados dos Testes

### Testes Específicos do P08 (`tests/test_gates_p08.py`)

```text
tests/test_gates_p08.py::test_wilson_matches_reference_values PASSED
tests/test_gates_p08.py::test_fdr_uses_total_candidate_count PASSED
tests/test_gates_p08.py::test_unstable_edge_fails_stability PASSED
tests/test_gates_p08.py::test_neighborhood_spike_fails PASSED
tests/test_gates_p08.py::test_injected_edge_is_approved PASSED
tests/test_gates_p08.py::test_coin_flip_approves_zero PASSED

6 passed in 16.23s
```

### Suíte Completa de Regressão

```text
python -m pytest
```

Resultado:

```text
276 passed, 3 skipped in 63.94s
```

Os 3 skips permanecem sendo os testes de integração com Supabase staging, que exigem `SUPABASE_STAGING_DB_URL`.

### Linters, Tipagem e Bytecode

```text
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall packages tools
```

Resultado:
- `ruff check`: 0 erros.
- `ruff format --check`: 110 arquivos formatados.
- `mypy`: 65 arquivos fonte tipados estritamente sem erros.
- `compileall`: compilação limpa sem erros.

---

## Critérios de Aceite

- [x] `test_coin_flip_approves_zero` verde em 3 seeds (101, 202, 303 com 2.000 candidatos cada $\to$ 0 aprovados).
- [x] Pipeline documentado em `research/README.md` com a ordem e o motivo de cada portão.
