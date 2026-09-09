# CAT-10 — Integração segura com candidatura e pipeline financeiro

Data: 2026-09-05.

## Resultado

CAT-10 foi iniciado e implementado localmente de forma incremental no BOT.

A entrega desta etapa adiciona flags explícitas para separar:

- caminho legado de entradas;
- shadow de indicadores;
- novo engine incremental.

O default continua preservando o comportamento auditado: legado ativo, shadow ativo e engine
incremental de entrada desligado.

Nenhuma ordem externa, login, corretora, Supabase remoto ou build foi executado.

## Implementação

- Criado `IqOptionExecutionFlags` em `apps/core/iqoption_auto_trader.py`.
- O `IqOptionAutoTrader` passa a receber `execution_flags_provider`.
- Se `legacy_entries_enabled=False` e `incremental_entries_enabled=False`, o trader restaura o
  estado local e bloqueia novas entradas com `IQOPTION_ENTRY_ENGINE_DISABLED`, sem buscar candles e
  sem submeter ordem.
- Se `incremental_entries_enabled=True`, o RSI local explícito `iqoption-rsi-demo` pode usar o
  cache incremental CAT-09 para gerar a decisão.
- Mesmo no caminho incremental, a decisão continua passando pelos gates existentes:
  `resolve_candidates`, bot armado, uma ordem em voo, candle pós-arm, risco local, payout/ticket,
  validação final sob lock e `runtime.submit`.
- Estratégias de manifesto/famílias continuam no caminho legado enquanto o compilador completo de
  família para DAG incremental não for implementado. Com legado desligado, elas retornam
  `INCREMENTAL_ENGINE_UNSUPPORTED` e não enviam ordem.
- Shadow permanece incapaz de enviar ordem. Divergência em shadow emite evento, mas não cria
  fallback financeiro.

## Gates preservados

- Asset exato e timeframe governado pelo manifesto continuam sendo resolvidos por
  `resolve_candidates`.
- `DynamicManifestCatalog.is_eligible` continua sendo chamado em `_check_manifest_execution` antes
  da submissão.
- `LiveMonitor`/SPRT continua obrigatório para estratégias de manifesto.
- O ticket é revalidado em `validate_runtime_entry`, incluindo cliente/generation implícita,
  freshness do payout, operador armado e contexto de manifesto atual.
- UNKNOWN/reconciliação e Risk Ledger continuam pertencendo ao Core/writer.
- O caminho incremental não cria retry financeiro.

## Testes adicionados/atualizados

Arquivo principal:

- `tests/unit/test_iqoption_auto_trader.py`

Novas provas:

- engine incremental RSI é explícito e ainda passa pelo `CoreRuntime.submit`;
- desabilitar todos os engines não busca market data e não submete ordem;
- família de manifesto não cai para legado quando legado está desligado e incremental ainda não
  suporta família completa.

## Validação executada

```text
python -m pytest tests/unit/test_iqoption_auto_trader.py tests/unit/test_iqoption_failure_recovery.py tests/unit/test_indicator_cache.py -q
60 passed

python -m pytest tests/integration/test_manifest_execution_gates.py tests/unit/test_iqoption_candidates.py tests/replay/test_iqoption_failure_recovery_24h.py -q
54 passed

python -m ruff check apps/core/iqoption_auto_trader.py tests/unit/test_iqoption_auto_trader.py
All checks passed

python -m ruff format --check apps/core/iqoption_auto_trader.py tests/unit/test_iqoption_auto_trader.py
OK

python -m mypy apps/core/iqoption_auto_trader.py
Success
```

## Limites desta etapa

- O engine incremental completo para famílias F1..F5 ainda não foi ligado.
- O hot path ainda será medido de forma completa em CAT-11/CAT-13, quando houver replay de
  portfólio e benchmark.
- Não foi gerado EXE.

## Próximo passo

CAT-11 deve medir a frequência realmente executável do portfólio, separando sinais brutos de
operações possíveis sob uma ordem em voo, payout, risco, cooldown, candle atrasado e demais gates.
