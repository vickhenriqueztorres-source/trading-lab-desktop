# Strategy Lab — Plano de Prompts (engenharia de prompt, ponta a ponta)

Este documento contém **todos os prompts** necessários para construir o Strategy Lab do zero
até operação sem toque, na ordem em que devem ser executados. Cada prompt é autocontido,
referencia os IDs do PRD, e termina com critério de aceite verificável.

## Como usar

1. Copie o **Preâmbulo** (abaixo) para o topo de **todo** prompt antes de enviar ao agente.
2. Execute os prompts na ordem. Um prompt = um PR. Não avance com a suíte vermelha.
3. Se o agente responder "pare e reporte", leia o relatório, ajuste o prompt (ou este plano) e
   reenvie. Nunca peça para ele "decidir sozinho".
4. Antes de cada prompt, cole também `04-AGENTS.md` no contexto (ou garanta que está no repo).

Dependências entre prompts:

```text
P01 primitives ─┬─▶ P03 vendor+adapter ─▶ P04 collect ─▶ P05 schema ─▶ P06 hub ─────┐
P02 manifest ───┼─▶ P07 research core ─▶ P08 gates ─────────────────────────────────┤
                └─▶ P09 bot manifest_client + contrato de conformidade ──────────────┤
                                                                                     ▼
                                                    P10 publish ─▶ P11 catálogo+payout_gate
                                                                          │
                                                    P14 grammar+holdout ◀───┤
                                                                          ▼
                                                    P12 SPRT+uploader ─▶ P13 UI ─▶ P15 operação
```

---

## Preâmbulo (colar em todo prompt)

````markdown
Repositório: `trading-lab-desktop`; subprojeto autônomo: `strategy-lab/`. Leia antes de qualquer
edição: `strategy-lab/04-AGENTS.md`, `strategy-lab/01-ARCHITECTURE.md`,
`strategy-lab/03-PRD.md`. As invariantes I-1..I-14 do
AGENTS.md são absolutas. Se a estrutura real dos arquivos divergir do descrito neste prompt, ou se
qualquer instrução conflitar com uma invariante, PARE E REPORTE (o que foi pedido, o que encontrou,
opções, recomendação) antes de decidir sozinho.

O Strategy Lab possui `pyproject.toml`, lock, ambiente, estado e build próprios e não entra no EXE
principal. São proibidos imports cruzados, IPC, banco e arquivos privados compartilhados. A única
integração operacional com o bot é o manifesto assinado/versionado. Caminhos não qualificados neste
prompt são relativos a `strategy-lab/`; prompts do bot declaram explicitamente o projeto principal.

Backend: Supabase. Conector IQ Option: `vendor/iqoptionapi` (vendorizado). Python 3.12, `ruff`,
`mypy --strict`, `pytest`. Sem dependência nova salvo as listadas na seção "Dependências autorizadas".

Entregável padrão de todo prompt: (1) diff; (2) suíte inteira verde + `ruff` + `mypy`; (3) entrada
em `WORKLOG.md` com IDs R-* cobertos, decisões e o que ficou fora; (4) resumo final com a prova de
cada critério de aceite listado.
````

---

## P01 — `packages/primitives`: indicadores incrementais tipados + paridade

````markdown
# TAREFA P01: criar o pacote `packages/primitives` (R-PRIM-1..7)

## Objetivo
Implementação de referência isolada dos indicadores incrementais, tipados por categoria e em
Decimal. O bot mantém implementação local compatível; versão, vetor público e hash de paridade
formam o contrato numérico, sem import entre os produtos.

## Implementar
1. `packages/primitives/pyproject.toml` (nome `tl-primitives`), `VERSION = "1.0.0"`.
2. `primitives/base.py`:
   - `class Candle(BaseModel)`: `ts: int` (múltiplo de 60), `o,h,l,c: Decimal`, `tick_vol: int`;
     validador `l <= min(o,c) <= max(o,c) <= h`. `strict=True, extra="forbid"`.
   - `class Category(StrEnum)`: REGIME, TRIGGER, CONFIRM.
   - `class Indicator(ABC)`: atributos de classe `category: Category`, `name: str`,
     `param_spec: dict[str, ParamRange]`; métodos `update(candle) -> Output | None`
     (None enquanto aquece), `warmup_required -> int`, `reset()`.
   - `class ParamRange(BaseModel)`: `min, max, step` (Decimal ou int), `kind: "int"|"decimal"`.
   - Contexto Decimal fixado no import: `getcontext().prec = 28`, `rounding = ROUND_HALF_EVEN`.
3. Indicadores (cada um em arquivo próprio, ordem de operações documentada em docstring):
   - REGIME: `adx.py` (Wilder, len), `bb_width_ratio.py` (largura BB / mediana de N larguras),
     `ema_alignment.py` (EMA a>b>c e inclinação), `session_window.py` (hora UTC ∈ [ini,fim)).
   - TRIGGER: `bb_close_outside.py`, `ema_pullback.py`, `level_touch.py` (níveis de TF maior
     injetados), `range_break.py` (N velas), `quadrant_majority.py` (minutos 2-4 / 7-9).
   - CONFIRM: `candle_rejection.py` (corpo/range e pavio), `rsi_extreme.py` (Wilder),
     `stoch_cross.py`, `rsi_divergence.py`, `tick_volume_ratio.py`.
   Saída padronizada: `Output(direction: Literal["call","put","none"], value: Decimal | None,
   meta: dict[str, Decimal])`.
4. `primitives/registry.py`: `REGISTRY: dict[str, type[Indicator]]`, `by_category(cat)`.
5. Teste de paridade (R-PRIM-6): `tests/parity/series_10k.json` (gerar uma vez com seed 20260902,
   passeio aleatório com volatilidade agrupada, commitado), `test_primitives_parity_hash.py`
   alimenta todos os indicadores com parâmetros default e compara SHA-256 da lista de outputs
   serializados canonicamente com `tests/parity/EXPECTED_SHA256`. Documentar no README: qualquer
   mudança de hash exige bump de VERSION. O vetor, a ordem canônica e o hash são artefatos públicos
   de conformidade que o bot reproduzirá em sua própria suíte; não há import cruzado.
6. Testes unitários por indicador com casos fixos calculados à mão (≥ 3 por indicador).
7. Teste `test_no_float_in_primitives`: AST scan proíbe literais float e `float(` no pacote.

## Dependências autorizadas
pydantic>=2, pytest. Nada mais.

## Critérios de aceite
- `pytest packages/primitives` verde; cobertura ≥ 90%.
- `EXPECTED_SHA256` commitado e o teste de paridade passa em duas máquinas diferentes
  (rodar 2× e comparar).
- Nenhum `float` no pacote (teste AST).
- Cada indicador declara `category` e `param_spec` completos.
````

---

## P02 — `packages/manifest_schema`: modelo, JSON Schema, assinatura

````markdown
# TAREFA P02: criar `packages/manifest_schema` (R-MAN-1..7)

## Objetivo
Definir o manifesto como contrato formal: pydantic + JSON Schema + assinatura Ed25519 canônica.
Usado por publish (lab), pela Edge Function `publish` (hub, via JSON Schema) e por
`manifest_client` (bot).

## Implementar
1. `manifest_schema/models.py`: `Manifest`, `StrategyEntry`, `Validated`, `Management`
   exatamente como Arquitetura §6, incluindo `primitives_version` e
   `primitives_parity_sha256`. Números como `str` decimal validados por regex
   `^-?\d+(\.\d+)?$` e convertíveis em Decimal. `status ∈ {approved, observation, rejected}`;
   `rejected` exige `reason_pt`. `expires_at - published_at <= 45*86400`.
2. Validação de `params` por família (R-MAN-3): `FAMILY_SPECS: dict[Family, dict[str, ParamRange]]`
   derivado de `primitives.REGISTRY` (importa `tl-primitives`). Fora da faixa → ValidationError.
3. `manifest_schema/canonical.py`: `canonical_bytes(manifest_dict_sem_signature) -> bytes`
   (JSON com `sort_keys=True, separators=(",",":"), ensure_ascii=False`).
4. `manifest_schema/signing.py`: `sign(manifest, private_key, key_id) -> Manifest`,
   `verify(manifest, public_keys: dict[str, bytes]) -> bool`. Ed25519 via `cryptography`.
5. `manifest_schema/export.py`: gera `schema/manifest.v1.schema.json` (commitado) para uso
   em Deno na Edge Function.
6. `tests/keys/`: par de chaves **de teste** (público por design) + README avisando.
7. Testes: round-trip sign/verify; assinatura alterada em 1 byte falha; `key_id` desconhecido
   falha; `params` fora de faixa falha; expiração > 45d falha; número como float falha;
   JSON Schema exportado valida o mesmo exemplo que o pydantic aceita e rejeita os mesmos casos.
8. `contracts/manifest_acceptance_vectors.json`: vetores públicos canônicos com casos aceitos e
   rejeitados, reason code esperado e hash próprio. Lab e bot executam os mesmos vetores sem
   importar código um do outro.

## Dependências autorizadas
pydantic>=2, cryptography, jsonschema (só em testes).

## Critérios de aceite
- `schema/manifest.v1.schema.json` commitado e sincronizado (teste compara com export).
- 100% dos casos hostis rejeitados.
- Exemplo da Arquitetura §6 validado e assinado com chave de teste em `tests/fixtures/manifest_example.json`.
````

---

## P03 — `vendor/iqoptionapi` + adaptador `iq_client`

````markdown
# TAREFA P03: vendorizar `iqoptionapi` e criar o adaptador isolado (R-VEND-1..3)

## Objetivo
Trazer a biblioteca para o repo, congelar, e isolá-la atrás de uma interface própria com fakes
para teste. Nada além de `iq_client.py` a importa.

## Implementar
1. `vendor/iqoptionapi/`: cópia do fork escolhido, com `LICENSE`, `UPSTREAM_COMMIT` (hash),
   `PATCHES.md` (vazio inicialmente, formato: data, motivo, arquivos). Não modificar o código
   vendorizado neste prompt.
2. `tools/strategy_lab/collect/iq_client.py`:
   - `class Candle` reexportada de `primitives`.
   - `class IQClientProtocol(Protocol)`: `login()`, `logout()`,
     `fetch_candles(asset: str, tf_s: int, n: int, end_ts: int) -> list[Candle]`,
     `fetch_payout(asset: str) -> Decimal | None`, `list_assets() -> list[str]`.
   - `class IQClient(IQClientProtocol)`: implementação sobre o vendor. Converte cada vela para
     `Candle` via pydantic (Decimal a partir de `str(valor)`), descarta nada silenciosamente:
     vela inválida levanta `InvalidCandleError` com o payload bruto (sem credenciais).
     Pausa aleatória 0,5–2,0 s entre chamadas (injetável para teste). Mapeia `asset` para o
     id interno da API; sufixo `-OTC` preservado no nome canônico.
   - `class FakeIQClient(IQClientProtocol)`: lê `tests/fixtures/iq/*.json`.
3. `tools/strategy_lab/collect/recorder.py`: CLI `strategy-lab record-fixture --asset --from --to`
   grava resposta real em fixture (uso manual, uma vez; scrub de qualquer campo não-preço).
4. Lint (R-VEND-2): teste `test_vendor_import_boundary` faz AST scan do repo e falha se
   `iqoptionapi` for importado fora de `iq_client.py`.
5. Testes com `FakeIQClient`: conversão Decimal, erro em vela inválida, pausa injetável,
   `-OTC` preservado.

## Dependências autorizadas
As do vendor (listar em `vendor/iqoptionapi/REQUIREMENTS.txt`, pinadas). pydantic.

## Critérios de aceite
- `test_vendor_import_boundary` verde.
- Nenhum arquivo do vendor alterado (diff vazio contra UPSTREAM_COMMIT).
- Fixture de ao menos 1 asset × 1.000 velas gravada e commitada (dados de preço públicos).
````

---

## P04 — `collect`: canário, backfill idempotente, gaps, payout, invariantes

````markdown
# TAREFA P04: implementar `strategy-lab collect` (R-COL-1..13)

## Objetivo
Job diário, stateless e idempotente, que leva o Supabase ao estado "todas as velas fechadas até
agora, validadas", com canário de formato e invariantes, sem jamais gravar lixo.

## Pré-requisito
P05 (schema Supabase) pode ser executado em paralelo; este prompt usa um `Repository` com
interface própria e um `FakeRepository` em memória. A implementação Postgres vem em P05.

## Implementar
1. `collect/clock.py`: `Clock` injetável (`now_ts()`), `check_ntp(max_skew_s=5)` (R-COL-1).
2. `collect/credentials.py`: keyring do SO (`keyring` lib) com fallback a env `IQ_EMAIL/IQ_PASSWORD`
   apenas se `STRATEGY_LAB_ENV=vps`. Nunca loga valores.
3. `collect/canary.py` (R-COL-2): `CANARY = [(asset, ts), ...]` (5 velas) + `tests/fixtures/canary.json`;
   `run_canary(client) -> None | CanaryMismatch` comparando o,h,l,c exatos.
4. `collect/repository.py`: `Protocol` com `watermark(asset) -> int | None`,
   `upsert_candles(list[Candle], source) -> int`, `record_gaps(...)`, `upsert_payout(asset, hour_ts, value)`,
   `record_run(report)`. `FakeRepository` em memória.
5. `collect/backfill.py` (R-COL-3..6): loop por watermark até `floor(now,60)-60`, lotes ≤ 1000,
   validação por vela, abort total em vela inválida, UPSERT por lote.
6. `collect/sessions.py` + `collect/gaps.py` (R-COL-7): grade de 60 s ∩ `market_sessions`;
   `in_session` flag. Calendário inicial: Forex seg 00:00–sex 21:00 UTC; `-OTC` sáb 00:00–dom 24:00.
7. `collect/payout_sampler.py` (R-COL-8): média incremental, `samples += 1`.
8. `collect/invariants.py` (R-COL-9): monotonicidade, duplicata, salto > 8·ATR(14) → `suspect`.
9. `collect/runner.py`: orquestra 0→6 da Arquitetura §4; relatório JSON (R-COL-10); flags
   `--payout-only` (R-COL-11), `--assets`, `--dry-run`.
10. `cli.py`: `strategy-lab collect`, `strategy-lab status` (R-COL-13; usa `Repository`).
11. Testes (FakeIQClient + FakeRepository + Clock):
    - `test_canary_fixture_matches` (CI intocável) e `test_canary_mismatch_aborts_before_write`.
    - `test_backfill_is_idempotent` (3 runs = 1 run).
    - `test_dst_and_current_candle_never_written` (CI intocável): clock em 2026-03-29 02:30
      local/Europa e em UTC; vela corrente ausente do repo em todos os casos.
    - `test_invalid_candle_aborts_run_zero_writes`.
    - `test_gaps_classified_by_session`.
    - `test_payout_hours_without_run_stay_zero_samples`.
    - `test_invariant_jump_marks_suspect`.
    - `test_no_secrets_in_logs` (captura logs, procura email/senha fake).

## Dependências autorizadas
keyring, ntplib (ou `time.clock_gettime` + servidor NTP simples), pydantic.

## Critérios de aceite
- Todos os testes acima verdes; cobertura ≥ 90% em `collect/`.
- `strategy-lab collect --dry-run` com FakeIQClient imprime relatório completo.
- Nenhum `time.time()`/`datetime.now()` sem tz (teste AST).
````

---

## P05 — Supabase: migrations, RLS, staging, `PostgresRepository`

````markdown
# TAREFA P05: schema Supabase, RLS e repositório Postgres (R-HUB-1, R-HUB-2, R-HUB-7, R-HUB-8)

## Implementar
1. `apps/hub/supabase/migrations/0001_schema.sql`: tabelas da Arquitetura §3 com todos os `check`.
   Índices: `candles(asset, ts desc)`, `payouts(asset, hour_ts)`, `live_outcomes(strategy_key, ts)`.
2. `0002_rls.sql`: RLS em todas as tabelas. Políticas: anon → nenhuma leitura; anon → `insert`
   em `live_outcomes` com `client_id = (auth.jwt()->>'client_id')::uuid`. `authenticated` não
   usado. Service role bypass (padrão).
3. `0003_sessions_seed.sql`: seed de `market_sessions` (Forex + OTC).
4. `0004_archive_cron.sql` (R-HUB-7): função `archive_old_candles()` que exporta para Storage via
   `pg_net`/Edge Function `archive` (criar stub) e apaga só após verificar contagem; `pg_cron`
   diário 03:00 UTC. Se `pg_net` não estiver disponível no free tier, PARE E REPORTE com alternativa
   (job no `collect --archive`).
5. `tools/strategy_lab/collect/pg_repository.py`: `PostgresRepository(Repository)` com `psycopg`
   (v3), UPSERT `ON CONFLICT (asset, ts) DO UPDATE ... WHERE candles.source = EXCLUDED.source`
   (R-COL-6), `--force-source` para sobrescrever. Conexão via `SUPABASE_DB_URL` (service).
6. Staging (R-HUB-8): `scripts/supabase_staging.sh` aplica migrations no projeto de staging;
   `tests/conftest.py` exige `SUPABASE_STAGING_DB_URL` e **falha** se a URL contiver o ref do
   projeto de produção (`SUPABASE_PROD_REF` em env).
7. Testes de integração (marcados `@pytest.mark.staging`, pulados sem env): UPSERT idempotente
   real; `check` rejeita vela inválida; RLS: cliente anon com JWT de teste insere em
   `live_outcomes` e **não** lê `candles`/`manifests` (deve receber 0 linhas ou erro).
8. `strategy-lab backup` (R-OPS-1): `pg_dump` para `~/strategy-lab/backups/YYYYMMDD.sql.gz.age`
   (criptografado com `age`, chave pública em config); alerta em `status` se > 8 dias.

## Dependências autorizadas
psycopg[binary]>=3, supabase (Python, só para Storage em P06), age (binário externo, documentado).

## Critérios de aceite
- Migrations aplicam do zero em staging sem erro.
- Testes staging verdes; teste de RLS prova negação de leitura anon.
- `collect` real (1 asset, `--dry-run` desligado) grava em staging e o 2º run grava 0 velas novas.
````

---

## P06 — Hub: Edge Functions `publish`, `outcomes`, `mirror` + Storage

````markdown
# TAREFA P06: Edge Functions do hub (R-HUB-3..6)

## Implementar (Deno/TypeScript estrito, `apps/hub/supabase/functions/`)
1. `_shared/ed25519.ts`: verificação com WebCrypto; chaves públicas A e B em env
   (`MANIFEST_PUBKEY_A`, `MANIFEST_PUBKEY_B`); `MANIFEST_TEST_PUBKEY` só aceita se
   `HUB_ENV=staging`.
2. `_shared/canonical.ts`: mesma canonicalização de P02 (teste de paridade cross-language: fixture
   `manifest_example.json` assinada em Python deve verificar em Deno).
3. `publish/index.ts` (R-HUB-3): POST body = manifesto. Passos: parse → JSON Schema
   (`schema/manifest.v1.schema.json` embutido) → verify(key_id) → `select max(manifest_version)`;
   se `<=` → 409 → upload `manifests/v{n}.json` e `manifests/current.json` (Storage, upsert,
   `cacheControl: "900"`, contentType json) → insert `manifests` → invoke `mirror` (fire-and-forget,
   log em falha). Resposta 201 com sha256.
4. `outcomes/index.ts` (R-HUB-4): JWT anônimo (`client_id` claim); lote ≤ 500; rejeita `ts` futuro
   ou < now−7d; rate-limit 60/h por client (tabela `rate_limits` ou header do Supabase); insert
   `ON CONFLICT DO NOTHING`.
5. `mirror/index.ts` (R-HUB-5): S3 API do R2 (`R2_ENDPOINT`, `R2_BUCKET`, chaves) — copia os dois
   objetos. Idempotente.
6. `client_token/index.ts`: emite JWT anônimo para um `client_id` novo (UUID gerado no bot), TTL
   1 ano, claim `client_id`. Sem login.
7. Storage: bucket `manifests` público de leitura; bucket `parquet` privado. Documentar em
   `apps/hub/README.md` com os comandos `supabase storage` para criar.
8. Testes (Deno test, com fakes de Storage/DB): assinatura A ok, B ok, inválida 401, chave de teste
   em prod 401, versão regressiva 409, schema inválido 422, outcomes com ts futuro 422, rate-limit
   429, canonicalização cross-language.

## Critérios de aceite
- `supabase functions serve` local + `curl` do exemplo assinado → 201; reenvio → 409.
- `GET .../storage/v1/object/public/manifests/current.json` retorna ETag e Cache-Control.
- Teste cross-language de canonicalização verde.
````

---

## P07 — `research` núcleo: dataset, simulador fim-de-vela, replay, cobertura

````markdown
# TAREFA P07: núcleo do `research` — dataset, simulação e replay (R-RES-1, R-RES-4..6, R-RES-10 parcial)

## Implementar (`tools/strategy_lab/research/`)
1. `dataset.py`: carrega `candles`+`payouts` do Supabase (via `psycopg`) ou de Parquet local
   (DuckDB) para Polars; grade de 60 s por asset; `coverage(asset, from, to) -> Decimal`;
   `refuse_if_coverage_below(0.95)` e gaps in_session não resolvidos (R-RES-1).
2. `payout_lookup.py`: `payout(asset, ts) -> Decimal | None` com `hour_ts = ts - ts % 3600`;
   None se `samples == 0`.
3. `outcome.py` (R-RES-4): `settle(direction, c_t, c_t1) -> won: bool` (empate = perda).
4. `vector_scan.py`: triagem vetorizada (Polars) de um candidato: gera sinais com versão
   vetorizada **somente para triagem**; nunca aprova.
5. `replay_simulator.py` (R-RES-5): instancia os 3 primitivos do candidato do `REGISTRY`,
   alimenta vela a vela, coleta `(ts, direction)` quando os três concordam, liquida em `t+1`,
   aplica `payout(asset, t)`; exclui operação se payout None. Retorna `TradeLog`.
6. `delay_penalty.py` (R-RES-6): aplica −0,5 pp e −1,0 pp a `p_hat` (documentar como: reclassifica
   aleatoriamente com seed uma fração de vitórias em derrotas, ou subtrai da estatística —
   escolher a subtração direta, determinística; registrar).
7. `candidate.py`: `Candidate(family, regime, trigger, confirm, params, tf, hours, asset)` com
   `hash()` estável.
8. `synthetic.py`: geradores para teste — passeio aleatório (seed), série com edge injetado
   conhecido (p=0,60 em regime marcado), série com lookahead-oráculo.
9. Testes:
   - `test_replay_never_sees_future`: instrumentar `Indicator.update` e provar que em `t` só
     recebeu velas ≤ t.
   - `test_settle_tie_is_loss`.
   - `test_payout_none_excludes_trade`.
   - `test_vector_scan_matches_replay_on_signal_timestamps` (paridade triagem ↔ replay ≥ 99%).
   - `test_injected_edge_recovered`: série com p=0,60 → replay mede 0,58–0,62.
   - `test_reverse_oracle_lookahead_detected`: candidato que usa `t+1` (fixture proposital) →
     p̂ > 0,95, provando que o detector de lookahead (I-4) funciona.

## Dependências autorizadas
polars, duckdb, numpy, psycopg, pyarrow.

## Critérios de aceite
- Todos os testes verdes; `test_replay_never_sees_future` cobre os 14 primitivos.
- `research --coverage-report` imprime cobertura por asset e recusa < 95%.
````

---

## P08 — `research` portões estatísticos + teste da moeda

````markdown
# TAREFA P08: portões estatísticos (R-RES-7, R-RES-8, R-RES-10)

## Implementar (`research/gates/`)
1. `wilson.py`: limite inferior 95% (z=1.959964) em Decimal.
2. `walk_forward.py`: janelas ancoradas treino 6 m / teste 2 m rolando; retorna p̂ por janela
   (só teste). Estabilidade: nenhuma janela < p_min; desvio-padrão entre janelas < 3 pp.
3. `multiple_testing.py`: Benjamini-Hochberg a 5% sobre p-valores binomiais de **todos** os
   candidatos da rodada (N registrado); permutação: embaralhar W/L 1.000× com seed, exigir p̂
   real > percentil 99.
4. `neighborhood.py`: perturba cada parâmetro ±15% (na grade de `param_spec`), re-simula via
   replay, exige que a mediana da vizinhança passe p_min + 1,5 pp.
5. `pbo.py`: CSCV com 16 blocos; PBO < 20%.
6. `pipeline.py`: ordem fixa walk-forward → estabilidade → FDR+permutação → vizinhança → PBO;
   cada portão devolve `GateResult(passed, metrics)`; curto-circuito em falha; tudo registrado.
7. `approve.py` (R-RES-8): aprovado se Wilson inferior (após penalidade pessimista) ≥ p_min + 1,5 pp,
   n ≥ 500 fora da amostra, todos os portões ok.
8. Testes:
   - `test_coin_flip_approves_zero` (CI intocável): 2.000 candidatos aleatórios sobre passeio
     aleatório (seed fixo) → 0 aprovados. Rodar com 3 seeds.
   - `test_injected_edge_is_approved`: série com p=0,60 estável → aprovado.
   - `test_unstable_edge_fails_stability`: p=0,62 em metade das janelas, 0,50 na outra → reprovado.
   - `test_neighborhood_spike_fails`: edge só em um ponto exato de parâmetro → reprovado.
   - `test_fdr_uses_total_candidate_count`.
   - `test_wilson_matches_reference_values` (tabela conhecida).

## Critérios de aceite
- `test_coin_flip_approves_zero` verde em 3 seeds.
- Pipeline documentado em `research/README.md` com a ordem e o motivo de cada portão.
````

---

## P09 — Bot: `manifest_client` fail-closed e contrato de conformidade

````markdown
# TAREFA P09: `manifest_client` no trading-lab-desktop (R-ISO-2..6, R-BOT-1..4)

Repositório alvo: o projeto principal `trading-lab-desktop`, fora do ambiente, estado e build do
Strategy Lab. Leia também `AGENTS.md`, `RULES.md` e `AIGUARD.md` do bot. É proibido importar módulos
de `strategy-lab/`; a única entrada operacional é o conteúdo do manifesto obtido por HTTPS/cache.

## Implementar (`apps/core/manifest_client.py` + `apps/core/manifest_keys.py`)
1. `manifest_keys.py`: `PUBLIC_KEYS = {"A": bytes, "B": bytes}` embutidas; `TEST_KEY` incluída
   **apenas** se `BUILD_PROFILE == "test"` (constante de build; teste prova ausência em prod).
2. `ManifestClient(clock, http, cache_dir, public_keys, primitives_version)`:
   - `poll()` a cada 900 s (`time.monotonic()`); `GET` primário (Supabase Storage URL) com
     `If-None-Match`; 304 → nada; falha → tenta espelho R2; falha → mantém cache.
   - `accept(manifest_json, response_date_header) -> Accepted | Rejected(reason_code)`:
     schema → assinatura (A/B) → `primitives_version == instalada` → `manifest_version > cache`
     → `primitives_version` e `primitives_parity_sha256` iguais ao build local → expiração vs
     `Date` do CDN (offline: tolerância 24 h) → faixas de params.
     Qualquer falha: `MANIFEST_REJECTED_<MOTIVO>` (novos códigos), mantém anterior.
   - Cache atômico: `manifest.json.tmp` → `fsync` → `os.replace`. Na leitura, verifica assinatura;
     corrompido → descarta e força poll.
   - `current() -> Manifest | None`; `is_expired() -> bool`; `on_change(callback)`.
3. Eventos: `manifest_applied(version)`, `manifest_rejected(reason)`, `manifest_expired`.
4. Testes com servidor HTTP fake local e chave de teste (`test_hostile_manifests_rejected`, CI
   intocável): assinatura inválida, chave de teste em build prod, versão regressiva, primitives
   divergente, params fora de faixa, expirado (por `Date`), cache truncado, cache com assinatura
   alterada, primário fora → espelho ok, ambos fora → cache mantido. Em TODOS, o manifesto
   anterior permanece ativo.
5. Teste `test_no_network_in_evaluation_cycle`: `evaluate_once` nunca chama `poll()` (poll roda em
   thread própria, entrega por fila).
6. Executar no bot todos os casos de `strategy-lab/contracts/manifest_acceptance_vectors.json` e
   o vetor público de paridade. O teste lê/copia somente esses artefatos públicos de contrato; não
   importa nem executa código do laboratório.
7. Testes de isolamento: AST/import scan proíbe `strategy-lab` no código do bot; inspeção do build
   prova que o EXE principal não contém módulos, banco, configuração, credenciais ou dependências
   exclusivas do laboratório.

## Critérios de aceite
- `test_hostile_manifests_rejected` verde (≥ 10 cenários).
- Build prod: `TEST_KEY` ausente (teste inspeciona módulo).
- Nenhum acesso a rede/disco no ciclo de avaliação.
- Vetores de conformidade aceitos/rejeitados com os mesmos reason codes do contrato.
- Build principal comprovadamente não contém o Strategy Lab.
````

---

## P10 — `publish`: montagem, preflight, diff, assinatura, upload

````markdown
# TAREFA P10: `strategy-lab publish` (R-PUB-1..5, R-RES-9, R-RES-11, R-ISO-2..3)

## Pré-requisito
P09 concluído e seus vetores públicos de conformidade aceitos pela suíte do bot. O preflight não
importa código do projeto principal.

## Implementar
1. `research/scorer.py` (R-RES-9): `margin = wilson_lower - p_min`; `score = margin * sqrt(ops_per_day)`;
   `worst_streak`; `result_1000_ops_stake10 = 1000*(p̂*payout_med - (1-p̂))*10`;
   `payout_min` = menor payout na grade 0,70..0,95 (passo 0,01) tal que
   `wilson_lower ≥ 1/(1+payout) + 0,015`.
2. `research/report.py` (R-RES-11): `ranking.md` (tabela ordenada por score, com os 5 números e
   veredito por portão) e `candidates.json`; insere `research_runs`.
3. `publish/builder.py` (R-PUB-1): lê `candidates.json` do `--run-id`; `--include/--exclude` por key;
   toda entrada nova nasce `status=observation` (R-PUB-5); entradas já `approved` no manifesto
   vigente permanecem `approved` se ainda aprovadas na rodada; `--promote KEY` promove
   observation→approved apenas se `live_outcomes` mostrarem ≥ 200 ops ou ≥ 30 dias sem rejeição
   SPRT. Criar `strategy-lab/packages/sprt` como implementação de referência; P12 implementará a
   versão local do bot e provará paridade por vetores, sem import cruzado.
4. `publish/preflight.py` (R-PUB-2): valida schema, assinatura, versão, hash de paridade, expiração
   e faixas usando somente `manifest_schema` e um verificador de contrato local. Executa todos os
   casos de `contracts/manifest_acceptance_vectors.json` e exige equivalência com os resultados
   registrados pelo P09. É proibido importar `apps/core/manifest_client.py` ou qualquer módulo do
   projeto principal.
5. `publish/differ.py` (R-PUB-3): diff legível; prompt interativo exige digitar o número total de
   estratégias; `--yes` proibido.
6. `publish/signer.py` (R-PUB-4): carrega chave privada de `~/.strategy-lab/keys/{A,B}.pem`
   (verifica modo 0600; recusa outro); `--key-id`.
7. `publish/uploader.py`: POST para Edge Function `publish`; trata 201/401/409/422 com mensagens claras.
8. Testes: builder com fixtures; preflight rejeita cada vetor hostil com o reason code esperado;
   scan proíbe import do bot; differ; signer recusa arquivo 0644; uploader com servidor fake.

## Critérios de aceite
- `strategy-lab research` (dados sintéticos) → `ranking.md` + `candidates.json`.
- Preflight e bot apresentam paridade integral nos vetores públicos, sem import cruzado.
- `strategy-lab publish --run-id X --key-id A` contra staging → 201; repetir → 409.
- Todas as entradas novas em `observation`.
````

---

## P11 — Bot: catálogo dinâmico por manifesto + `payout_gate`

````markdown
# TAREFA P11: catálogo dinâmico e gate de payout (R-BOT-5, R-BOT-6, R-BOT-9, R-BOT-12, R-BOT-13)

## Implementar
1. `apps/core/families/`: 5 classes `F1Reversal`, `F2Pullback`, `F3LevelRejection`,
   `F4SqueezeBreak`, `F5Quadrant`, cada uma compondo 1 Regime + 1 Trigger + 1 Confirm de
   uma implementação local do bot, parametrizadas por `params` do manifesto. Ela deve produzir o
   mesmo hash do vetor público indicado por `primitives_parity_sha256`; é proibido importar a
   implementação do Strategy Lab.
   Interface igual às estratégias existentes do `strategy_catalog` (emitem sinal para a arbitragem).
2. `apps/core/manifest_catalog.py`: em `manifest_applied`, constrói instâncias por
   `StrategyEntry`; `strategy_key` = id no catálogo; entradas removidas são marcadas
   `retiring` e descartadas após liquidação da ordem em voo (R-BOT-9); `observation` só elegível
   em conta Demo (R-BOT-8 parcial).
3. `apps/core/payout_gate.py` (R-BOT-6): antes de cada ordem, lê payout atual do adaptador da
   corretora; `p_min_now = 1/(1+payout)`; bloqueia se `wilson_lower < p_min_now + 0.015` com
   motivo `PAYOUT_BELOW_VALIDATED_EDGE` e texto pt-BR: "Opera com payout ≥ {payout_min}%. Agora:
   {atual}% — aguardando." Também bloqueia se `payout < payout_min` do manifesto.
4. Redução do gate antigo (R-BOT-12): remover a catraca de break-even de `_performance_allows`;
   manter cooldown pós-loss e `max_consecutive_losses`. Registrar no WORKLOG a tabela antes/depois.
5. Filtro de horário: estratégia só elegível dentro de `hours_utc` (UTC, do relógio do sistema com
   `tz=UTC`; teste de DST).
6. Testes: instâncias criadas por manifesto de fixture; `retiring` só após liquidação; payout gate
   bloqueia/desbloqueia com payout variando; observation não opera em Real; DST; invariantes
   (1 em voo, sem float, monotonic, zero DB no ciclo — reaproveitar testes existentes).

## Critérios de aceite
- Manifesto novo com estratégia inédita → aparece no catálogo sem restart (teste).
- Payout cai abaixo de `payout_min` → bloqueio em ≤ 1 ordem (teste).
- Suíte antiga do bot continua verde.
````

---

## P12 — Bot: `live_monitor` (SPRT) + `outcomes_uploader`

````markdown
# TAREFA P12: SPRT ao vivo e upload anônimo de resultados (R-BOT-7, R-BOT-8, R-BOT-10)

## Implementar
1. `packages/sprt/`: implementação local do bot de `SPRT(p0, p1, alpha=0.05, beta=0.05)` em
   Decimal, compatível por vetores públicos com a referência criada em P10;
   `update(won) -> Decision(continue|accept_h0|reject_h0)`; limites `A = ln((1-β)/α)`,
   `B = ln(β/(1-α))`; estado serializável.
2. `apps/core/live_monitor.py`: por `strategy_key`, `p0 = wilson_lower`, `p1 = p_min_at_validation`;
   a cada liquidação, `update`; `reject_h0` → status local `observation`, evento
   `strategy_demoted(key, n, llr)`, motivo `STRATEGY_DEMOTED_BY_SPRT`; reset ao aplicar manifesto
   com `validated` diferente. Persistir estado via `SingleDatabaseWriter` (fora do ciclo).
3. Manifesto expirado (R-BOT-8): `manifest_expired` → todas → `observation`; UI avisa.
4. `apps/core/outcomes_uploader.py` (R-BOT-10): fila SQLite local; `enqueue(strategy_key, ts, won,
   payout_pct)` chamado na liquidação (fora do ciclo, via evento); thread envia lote a cada 300 s
   com JWT anônimo (obtido de `client_token` na 1ª execução, `client_id` UUID gerado e salvo);
   falha → mantém fila, backoff; nunca lança para o core. Payload exatamente 5 campos.
5. Testes: SPRT com p real = p0 → não rejeita em 1.000 ops (α); p real = p1 → rejeita em < 120
   ops (mediana em 100 seeds); demote emite evento e bloqueia Real; expirado → observation;
   uploader fail-silent com servidor fake fora; payload não contém nada além dos 5 campos
   (teste de schema estrito); nenhum acesso à fila no ciclo de avaliação.

## Critérios de aceite
- Detecção SPRT < 120 ops para Δ = 3 pp (prova em teste, mediana).
- Rede fora 30 dias simulados → operação intacta.
````

---

## P13 — Bot: UI de fichas (5 números, 3 estados)

````markdown
# TAREFA P13: painel de estratégias por manifesto (R-BOT-11, I-13)

## Implementar (GUI existente do trading-lab-desktop)
1. Substituir o painel de seleção por lista de fichas, uma por `StrategyEntry`, com exatamente:
   nome pt-BR · asset · TF · faixa horária; estado (Aprovada / Em observação / Reprovada);
   "Taxa de acerto validada {p_hat}% (mínimo necessário {p_min}%)"; "Margem de segurança
   +{margem} pp"; "Operações por dia ~{ops}"; "Pior sequência de perdas {streak} (em {n} operações)";
   "Resultado em 1.000 ops {valor} com stake $10, sem MG"; botão "Ligar"/"Desligar";
   "Ver detalhes ▸" (colapsável: payout_min, janelas, holdout, versão do manifesto).
2. Estado ao vivo na ficha: `Monitorando` / `Sinal` / `Bloqueada — {motivo legível}` (inclui
   `PAYOUT_BELOW_VALIDATED_EDGE` e cooldown mm:ss) / `Rebaixada pelo monitor`.
3. Painel secundário "Reprovadas — por quê": lista com `reason_pt` de uma frase.
4. Banner quando manifesto expirado ou rejeitado (com versão em uso e idade).
5. Modo de seleção (SINGLE default / MULTI) mantido do fix anterior; `observation` ligável só em Demo
   (botão desabilitado em Real com tooltip).
6. Proibições (teste de texto): nenhum número sem `n`; nenhuma taxa sem o mínimo ao lado; nenhuma
   ocorrência de "lucro garantido", "sem risco", "100%".
7. Testes de UI (framework existente): renderização das fichas a partir de manifesto fixture; estados;
   botão desabilitado em Real para observation; banner de expiração.

## Critérios de aceite
- Captura do painel com ≥ 3 fichas em estados distintos.
- Teste de texto proibido verde.
````

---

## P14 — `research`: gramática, holdout selado, descoberta

````markdown
# TAREFA P14: gramática de candidatos e holdout (R-RES-2, R-RES-3, R-RES-12)

## Implementar
1. `research/grammar.py` (R-RES-3): enumera candidatos = produto de (1 REGIME × 1 TRIGGER × 1 CONFIRM)
   × grade de params (`param_spec`, passo da grade; limitar a ≤ 5.000 por rodada com amostragem
   determinística por seed se exceder) × TF {M1,M5,M15} × faixas horárias
   {00-06, 06-10, 10-13, 13-16, 16-21, weekend} × assets. Exclui pares de primitivos incompatíveis
   declarados (`INCOMPATIBLE`), ex.: `rsi_extreme` com `quadrant_majority`. `total_candidates`
   registrado para o FDR.
2. `research/holdout.py` (R-RES-2): separa últimos 3 meses; grava `holdout_range` e hash em
   `research_runs`; `open_once(run_id)` só pode ser chamado uma vez por run (flag persistida);
   `burn(range)` registra faixa queimada em `holdout_burned` (tabela nova, migration aditiva) e
   a gramática da próxima rodada usa faixa diferente.
3. `research/runner.py`: pipeline completo da Arquitetura §5 (0→9), incluindo o passo 8 sanidade
   (série embaralhada da própria rodada → deve aprovar zero, senão run `aborted`).
4. `research/live_merge.py` (R-RES-12): `live_outcomes` agregados por `strategy_key` entram como
   janela extra fora da amostra no walk-forward das estratégias publicadas.
5. Novas oportunidades: entradas aprovadas cujo `key` não existe no manifesto vigente vão para
   `ranking.md` na seção "Novas oportunidades" com o texto da ficha em pt-BR.
6. Testes: gramática nunca gera 2 da mesma categoria; `total_candidates` correto; holdout aberto 2×
   → erro; faixa queimada não reutilizada; sanidade embaralhada aborta run se aprovar; live_merge
   reduz p̂ quando ao vivo é pior.

## Critérios de aceite
- `strategy-lab research --seed 1` sobre dados sintéticos com 1 edge injetado → aprova só ele.
- Sanidade embaralhada → 0 aprovados (log).
````

---

## P15 — CI, runbook, agendador, VPS

````markdown
# TAREFA P15: operação sem toque (R-OPS-1..4)

## Implementar
1. `.github/workflows/ci.yml`: `ruff`, `mypy`, `pytest` (sem marcadores `staging`) e os 5 testes
   intocáveis como job separado e obrigatório: `test_coin_flip_approves_zero`,
   `test_primitives_parity_hash`, `test_canary_fixture_matches`, `test_hostile_manifests_rejected`,
   `test_dst_and_current_candle_never_written`. Adicionar job de isolamento que proíbe imports
   cruzados e inspeciona o EXE principal para provar ausência do Strategy Lab. Deno test para o hub.
   Job `staging` opcional com secrets.
2. `scripts/scrub_secrets.py` + pre-commit: varre diffs por padrões de email/senha/JWT/pem.
3. `scripts/schedule_windows.ps1`: cria tarefas do Agendador: `collect` 07:30 e 19:30 local;
   `backup` domingo 08:00; `status` diário com notificação (toast) em alerta.
4. `deploy/vps/`: `install.sh`, `strategy-lab.service` (oneshot), timers systemd: `collect` diário,
   `collect --payout-only` horário, `backup` semanal; env em `/etc/strategy-lab/env` (0600).
5. `strategy-lab/RUNBOOK.md`: diário/semanal/mensal; incidentes (canário falhou; API mudou;
   projeto Supabase pausado; SPRT rebaixou; manifesto rejeitado no bot; chave A comprometida →
   usar B; restaurar backup); migração para VPS passo a passo; rotação de chave planejada.
6. `strategy-lab/CHECKLIST-RELEASE.md`: bump de primitives exige: novo hash de paridade,
   release do bot, manifesto novo com `primitives_version`, e período em que bots antigos rejeitam
   (documentar tolerância).

## Critérios de aceite
- CI verde no PR; os 5 testes aparecem como job obrigatório.
- `schedule_windows.ps1` executado cria as 4 tarefas (verificar com `Get-ScheduledTask`).
- RUNBOOK cobre todos os 11 pontos de falha da Arquitetura §9 com ação concreta.
````

---

## Checklist de encerramento do projeto

Após P15, o projeto está "pronto" quando você consegue marcar todos:

- [ ] `research` em série embaralhada aprova zero (log do run).
- [ ] Build do Strategy Lab e EXE principal são independentes; inspeção encontra zero módulo,
  estado, configuração ou dependência exclusiva do outro produto.
- [ ] `publish` → bot Demo mostra a estratégia nova em ≤ 15 min sem restart.
- [ ] Desligar a rede do PC de teste por 1 h → bot opera normalmente com cache.
- [ ] Simular payout abaixo de `payout_min` → ficha mostra "aguardando", nenhuma ordem.
- [ ] Alimentar `live_outcomes` sintéticos com p = p_min → SPRT rebaixa < 120 ops.
- [ ] `collect` agendado rodou 7 dias seguidos sem intervenção (`status` limpo).
- [ ] `backup` da semana existe e restaura em staging.
- [ ] Chave B assina um manifesto e o bot aceita (teste de rotação).
