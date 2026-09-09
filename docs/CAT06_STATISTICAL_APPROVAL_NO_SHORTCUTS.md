# CAT-06 — Aprovação estatística sem atalhos

Data: 2026-09-05.

Produto alterado: Strategy Lab. Nenhum Supabase remoto, corretora, ordem financeira ou build do
EXE foi executado nesta etapa.

## Objetivo

Remover atalhos no caminho de aprovação estatística do catálogo para que uma receita só possa
avançar quando os números vierem do `TradeLog` auditável, com gates registrados e sem campos de
relatório fabricados.

## Implementado

- `approve_candidate` agora registra explicitamente os gates preliminares:
  `payout_available`, `sample_size` e `pessimistic_wilson`.
- Falta de payout observado deixa de virar falso positivo com zero observações e passa a reprovar
  com `RES_PAYOUT_MISSING`.
- O ranking FDR/BH passou a ser calculado sobre o conjunto real de candidatos da rodada, usando
  p-values derivados dos `TradeLog`s antes da aprovação individual, em vez de `candidate_rank=1`.
- `windows_passed` em `candidates.json` passou a representar as janelas reais do gate
  `walk_forward`; o resumo total de gates foi separado em `gates_passed`.
- `holdout_passed` foi separado do estado `approved` no relatório de pesquisa. No runner real ele
  só é preenchido após abertura do holdout.
- Execução elegível para produção com candidato pré-aprovado agora falha fechado com
  `RES_HOLDOUT_WINDOW_TOO_SHORT` se o holdout separado tiver menos de 90 dias.
- Família desconhecida deixou de cair implicitamente em F1; agora falha com
  `RES_UNKNOWN_FAMILY`.
- Caminhos de permutação/FDR e PBO deixaram de converter probabilidades/retornos para `float`.
  A simulação de permutação usa `Decimal` + RNG inteiro determinístico; PBO usa matriz Decimal.
- O helper sintético de publicação continua existindo apenas como fixture de plumbing e marca
  seu holdout sintético explicitamente, sem afetar o runner real.
- `pyproject.toml` do Strategy Lab passou a excluir diretórios `build/` gerados de Ruff/mypy,
  evitando que artefatos locais dupliquem módulos reais.

## Testes adicionados

- `test_payout_missing_is_explicit_failure_not_zero_trades`.
- `test_production_eligible_run_rejects_short_holdout_fallback`.
- `test_candidate_fdr_rank_uses_round_p_values_not_loop_position`.
- `test_candidates_json_uses_real_gate_and_holdout_fields`.
- `test_unknown_family_is_not_translated_to_f1`.
- `test_permutation_and_pbo_approval_paths_do_not_call_float`.

## Validação executada

- Testes focados CAT06/P08/runner/CAT05: **23 passed**.
- Regressão de falhas P10/CAT03/CAT06: **10 passed**.
- Suíte integral Strategy Lab: **351 passed, 3 skipped**.
- `ruff check tools tests packages`: aprovado.
- `ruff format --check tools tests packages`: aprovado.
- `mypy`: aprovado em 83 arquivos.
- `compileall tools packages`: aprovado.
- Scanner de segredos raiz `scripts/scrub_secrets.py --all`: aprovado, sem segredos.
- `git diff --check`: aprovado.

Observação: o pytest em Windows emitiu aviso de limpeza de `pytest-current` após o encerramento.
O processo já havia retornado código de sucesso; não houve falha de teste.

## Não executado

- Supabase remoto/staging.
- Aplicação de migrations remotas.
- Coleta real.
- Publicação de manifesto.
- Corretora, login, ordem ou qualquer envio financeiro.
- Build de EXE.

## Veredito

CAT-06 implementado localmente e validado por suíte/linters. A aprovação de produção agora exige
evidência real, payout observado, gates rastreáveis, FDR de rodada e holdout independente suficiente.
