# CAT-03 — Dataset real, identidade, tempo e payout sem vazamento

Data: 2026-09-05. Produto alterado: Strategy Lab (`strategy-lab/`).
Escopo: coleta/repositório de payout, dataset de pesquisa, lookup de payout, CLI e runner.

## Resultado

CAT-03 foi implementado localmente. Nenhuma chamada Supabase remota, deploy, migration aplicada,
broker ou ordem financeira foi executada.

## Mudanças principais

- Dataset de pesquisa agora possui `DatasetSnapshot` com origem, fonte, ativo exato, timeframe,
  intervalo, schema, qualidade e fingerprint imutável.
- `EURUSD` e `EURUSD-OTC` são identidades diferentes. Não há normalização de ativo no dataset.
- Pesquisa real exige fonte explícita: `--synthetic`, `--supabase` ou Parquet completo. Ausência
  de fonte não escolhe dados sintéticos silenciosamente.
- Timeframes `M1`, `M5` e `M15` são agregados a partir de buckets M1 completos em UTC.
- Vela corrente ou parcial é recusada por `RES_CURRENT_CANDLE_FORBIDDEN`.
- Cobertura considera sessões de mercado e gaps `in_session`; intervalo fora de sessão não vira
  falta artificial.
- `tick_vol=None` permanece ausente. Família que depende de `tick_volume_ratio` fica inelegível
  com `RES_TICK_VOLUME_UNAVAILABLE`.
- Payout passou a ter observação pontual (`payout_observations`) e regra as-of. Média horária
  legada sem `observed_at` não é usada para sinal no início da hora.
- Relatórios de pesquisa incluem fingerprint do dataset e flag de elegibilidade de produção.

## Supabase

Foi criada apenas a migration local:

- `strategy-lab/apps/hub/supabase/migrations/0007_payout_observations.sql`

Ela adiciona `public.payout_observations` para preservar `observed_at`, `payout_pct` e `source`.
A migration não foi aplicada remotamente porque staging/credenciais seguras não estão configurados
neste ambiente. Os testes staging continuam pulados por ausência de `SUPABASE_STAGING_DB_URL`.

## Validação executada

Comandos executados no ambiente próprio do Lab (`strategy-lab/.venv`):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m compileall packages tools tests
python scripts\scrub_secrets.py --all
git diff --check
```

Resultados:

- `pytest`: 336 passed, 3 skipped.
- `ruff check`: aprovado.
- `ruff format --check`: aprovado.
- `mypy`: aprovado em 81 source files.
- `compileall`: aprovado.
- `scrub_secrets.py --all`: aprovado, 0 segredos detectados.
- `git diff --check`: aprovado.

Os 3 skips são testes staging Supabase sem `SUPABASE_STAGING_DB_URL`.
O pytest em Windows emitiu aviso de limpeza de diretório temporário `pytest-current` após exit 0;
isso não alterou o resultado da suíte.

## Testes adicionados

`strategy-lab/tests/test_cat03_dataset_contract.py` cobre:

- separação exata de ativo spot vs OTC;
- buckets completos M1/M5/M15;
- recusa de vela corrente;
- payout com ativo exato, samples zero, ponto futuro e legado sem as-of;
- volume ausente tornando F4 inelegível;
- gap fora de sessão sem bloqueio de cobertura;
- fingerprint de snapshot estável em três reexecuções e sensível à fonte;
- CLI recusando pesquisa sem fonte real ou `--synthetic` explícito.

## Limitações

- Migration Supabase não aplicada em staging ou produção.
- Não houve coleta real, inventário remoto, archive hot/cold, replay com dados reais ou publicação.
- Nenhuma estratégia nova foi aprovada por esta fase.
- Esta fase prepara a qualidade do dataset; a equivalência completa de replay/estratégia pertence
  ao CAT-04.
