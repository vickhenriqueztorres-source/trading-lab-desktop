# CAT-07 — Pesquisa ampla, limitada e compatível com o executor

Data: 2026-09-05.

Produto alterado: Strategy Lab. Nenhum Supabase remoto, corretora, ordem financeira ou build do
EXE foi executado nesta etapa.

## Objetivo

Ampliar a diversidade testável da pesquisa sem explodir a combinação de candidatos e sem gerar
receitas que o executor do bot não consegue rodar.

## Implementado

- A gramática passou a consumir um contrato local de capacidades do executor:
  famílias suportadas, timeframes suportados, disponibilidade de `tick_volume`, ativos suportados,
  limite de warmup e budget de trials por experimento.
- Criado budget nomeado `DEFAULT_TRIAL_BUDGET = 500`, dentro da faixa inicial CAT-07
  de 300–500 candidatos elegíveis por experimento.
- A enumeração passou a selecionar candidatos em fluxo, com amostragem determinística baseada em
  seed + hash estável. O caminho não precisa mais materializar todo o universo antes do cap.
- `GrammarResult.audit_report()` agora reporta:
  universo teórico, elegíveis, amostrados, budget, descartes por motivo e diversidade por família,
  ativo, timeframe e faixa horária.
- Os valores min/mid/max derivados de `ParamRange` agora permanecem dentro da grade declarada.
  Se `max` não cai exatamente no step, o último valor usado é o maior valor alinhado menor ou igual
  ao máximo.
- Investigação/correção F5: a família `F5 = session_window + quadrant_majority + rsi_extreme`
  estava no contrato público, mas era eliminada pela incompatibilidade genérica
  `quadrant_majority + rsi_extreme`. A correção permite apenas o trio canônico F5; combinações
  não canônicas com o mesmo par continuam proibidas.
- Família F3/`level_touch` deixou de usar defaults sintéticos 99/101 automaticamente. Candidatos
  com níveis absolutos exigem perfil explícito por ativo; sem perfil, são descartados com
  `ABSOLUTE_LEVEL_PROFILE_REQUIRED`.
- O CLI de `research` passou a retornar `grammar_audit` no JSON final.

## Audit de amostra padrão

Com `assets = EURUSD-OTC, GBPUSD-OTC`, `timeframes = M1, M5, M15`, seis janelas horárias e
`seed = 7`:

```json
{
  "theoretical_candidates": 116640,
  "eligible_candidates": 116640,
  "sampled_candidates": 500,
  "trial_budget": 500,
  "discarded_by_reason": {
    "ABSOLUTE_LEVEL_PROFILE_REQUIRED": 2
  },
  "diversity": {
    "families": {
      "F1": 123,
      "F2": 152,
      "F4": 119,
      "F5": 106
    },
    "assets": {
      "EURUSD-OTC": 243,
      "GBPUSD-OTC": 257
    },
    "timeframes": {
      "M1": 180,
      "M5": 151,
      "M15": 169
    },
    "hours": {
      "00-06": 85,
      "06-10": 64,
      "10-13": 78,
      "13-16": 85,
      "16-21": 104,
      "00-24": 84
    }
  }
}
```

## Testes adicionados/ajustados

- `test_default_trial_budget_is_named_and_bounded`.
- `test_same_inputs_same_bounded_sample`.
- `test_capabilities_prune_before_sample`.
- `test_f5_is_generated_only_as_canonical_family`.
- `test_absolute_level_family_requires_asset_profile`.
- `test_min_mid_max_values_stay_on_declared_grid`.
- Ajuste no teste de incompatibilidade para aceitar somente a exceção canônica F5.

## Validação executada

- Gramática/CAT07: **10 passed**.
- Suíte integral Strategy Lab: **357 passed, 3 skipped**.
- `ruff check tools tests packages`: aprovado.
- `ruff format --check tools tests packages`: aprovado.
- `mypy`: aprovado em 83 arquivos.
- Smoke CLI sintético: `strategy-lab research --synthetic --seed 7 --max-candidates 30`
  concluído com `status=ok`, `approved_count=1`, sem elegibilidade de produção.

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

CAT-07 implementado localmente. A pesquisa agora gera uma amostra ampla, limitada, reproduzível e
compatível com as capacidades declaradas do executor, sem meta de quantidade de aprovados.
