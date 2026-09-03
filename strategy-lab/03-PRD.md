# Strategy Lab — PRD (Product Requirements Document)

Versão 1.0 · Backend: Supabase · Conector: `iqoptionapi` vendorizado

Cada requisito tem ID estável (`R-<módulo>-<n>`). Prompts, código e testes referenciam esses IDs.
Prioridade: **P0** = sem isso o sistema não é confiável; **P1** = necessário para operar sem
toque; **P2** = melhoria.

---

## 0. Isolamento entre produtos

| ID | Requisito | P |
|---|---|---|
| R-ISO-1 | `strategy-lab/` possui `pyproject.toml`, lock, ambiente, configuração, estado e build próprios; nenhum desses itens entra no EXE principal. | P0 |
| R-ISO-2 | A única integração operacional Lab → bot é o manifesto JSON assinado/versionado por HTTPS e seu cache local validado. | P0 |
| R-ISO-3 | São proibidos imports cruzados, IPC, banco compartilhado e leitura direta de arquivos privados entre Strategy Lab e `trading-lab-desktop`. | P0 |
| R-ISO-4 | Falha, atualização ou encerramento do Strategy Lab não interrompe o bot; o bot usa o último manifesto válido até a expiração. | P0 |
| R-ISO-5 | O pipeline gera artefatos independentes e prova que o build principal não contém módulos, credenciais, estado ou dependências exclusivas do Strategy Lab. | P0 |
| R-ISO-6 | Compatibilidade numérica é provada pelo mesmo vetor público de 10.000 velas; `primitives_version` e `primitives_parity_sha256` são verificados no manifesto. | P0 |

## 1. Módulo `primitives` (implementação de referência isolada)

| ID | Requisito | P |
|---|---|---|
| R-PRIM-1 | Todo indicador é incremental (`update(candle) -> value`), com estado interno, sem recomputar a série. | P0 |
| R-PRIM-2 | Todo indicador é tipado em exatamente uma categoria: `Regime`, `Trigger`, `Confirm`. Tipo faz parte da assinatura da classe. | P0 |
| R-PRIM-3 | Aritmética em `Decimal` com `getcontext().prec` fixo (28) e ordem de operações canônica documentada por indicador. | P0 |
| R-PRIM-4 | Catálogo mínimo: Regime = ADX, BB width ratio, EMA alignment, session window; Trigger = BB close outside, EMA pullback, level touch, range break, quadrant majority; Confirm = candle rejection, RSI extreme, Stoch cross, RSI divergence, tick volume ratio. | P0 |
| R-PRIM-5 | Cada indicador declara faixas válidas de parâmetros (min, max, passo) usadas pelo JSON Schema do manifesto e pela gramática. | P0 |
| R-PRIM-6 | Teste de paridade: série pública fixa de 10.000 velas → lista de saídas → SHA-256 fixado. O bot executa o mesmo vetor com sua implementação local; qualquer mudança exige bump de `VERSION`. | P0 |
| R-PRIM-7 | `VERSION` semver; versão e hash de paridade são publicados no manifesto e incorporados no build compatível do bot, sem import cruzado. | P0 |

## 2. Módulo `manifest_schema`

| ID | Requisito | P |
|---|---|---|
| R-MAN-1 | Modelo pydantic + JSON Schema exportado, `schema_version` inteiro. | P0 |
| R-MAN-2 | Campos do manifesto: `primitives_version`, `primitives_parity_sha256` e, por estratégia, `key`, `family`, `display_name_pt`, `asset`, `timeframe`, `hours_utc`, `params`, `validated{...}`, `status`, `management`. Valores numéricos publicados como **string decimal**. | P0 |
| R-MAN-3 | `params` em strings decimais, obrigatórios por família conforme Arquitetura §6.1; faixas dos construtores derivadas de R-PRIM-5 e gates de composição declarados separadamente. Fora da faixa/grade ou relação inválida = manifesto inteiro inválido. | P0 |
| R-MAN-4 | Assinatura Ed25519 sobre o JSON canônico (chaves ordenadas, sem espaços) excluindo o campo `signature`; `key_id` ∈ {A, B}. | P0 |
| R-MAN-5 | `expires_at − published_at` ≤ 45 dias. | P0 |
| R-MAN-6 | `status` ∈ {`approved`, `observation`, `rejected`}; `rejected` carrega `reason_pt` de uma frase. | P0 |
| R-MAN-7 | `validated.payout_min` obrigatório: menor payout na grade 0,01 em que `wilson_lower ≥ 1/(1+payout) + 0,015`, arredondado para cima (Arquitetura §6.1). | P0 |

P02: estrutura JSON Schema + perfil semântico obrigatório `urn:strategy-lab:manifest-policy:v1`
(Arquitetura §6.2). O validador genérico sozinho não é suficiente. Versões estruturais, epochs,
horas e contagens são inteiros; valores financeiros e parâmetros são strings. O contrato local
não implementa o publish remoto, a aprovação de pesquisa ou a habilitação de operações.

## 3. Módulo `vendor/iqoptionapi`

| ID | Requisito | P |
|---|---|---|
| R-VEND-1 | Cópia integral com `LICENSE`, `UPSTREAM_COMMIT`, `PATCHES.md`; patches mínimos de segurança autorizados e verificados por diff e hashes antes/depois. Nenhum `pip install` da internet para este pacote. | P0 |
| R-VEND-2 | Único importador: `tools/strategy_lab/collect/iq_client.py`. Teste de lint proíbe `import iqoptionapi` fora dele. | P0 |
| R-VEND-3 | Interface do adaptador: `login()`, `logout()`, `fetch_candles(asset, tf_s, n, end_ts) -> list[Candle]`, `fetch_payout(asset) -> Decimal | None`, `list_assets() -> list[str]`. | P0 |

P03 autorizado: lock em vendor/REQUIREMENTS.txt, fora do snapshot; lint R-VEND-2 cobre
todo código próprio do Lab, excluindo auto-imports do terceiro. O aceite de fixture
real (1 ativo × 1.000 velas) permanece aberto até gravação manual comprovada.
Fakes e ensaio do recorder em diretório temporário não contam como dados externos.

## 4. Módulo `collect`

| ID | Requisito | P |
|---|---|---|
| R-COL-1 | Preflight: |relógio − NTP| < 5 s, senão aborta. Credenciais via keyring do SO (Windows Credential Manager / env na VPS). | P0 |
| R-COL-2 | Canário: 5 velas de `(asset, ts)` fixos comparadas com fixture antes de qualquer escrita. Divergência → aborta com relatório. | P0 |
| R-COL-3 | Backfill por watermark (`max(ts)` por asset) em lotes de até 1.000 até alcançar `floor(now, 60) − 60`. | P0 |
| R-COL-4 | Vela corrente nunca gravada (`ts < floor(now,60) − 60`). | P0 |
| R-COL-5 | Validação por vela (pydantic estrito + checks). Uma inválida → run abortado, zero escrita. | P0 |
| R-COL-6 | UPSERT idempotente `ON CONFLICT (asset, ts) DO UPDATE` só se `source` igual; nunca sobrescreve fonte diferente sem flag `--force-source`. | P0 |
| R-COL-7 | Gaps calculados contra a grade de 60 s dentro de `market_sessions`; `in_session` marcado. | P0 |
| R-COL-8 | Payout amostrado por asset na hora corrente (`samples += 1`, média incremental). Horas sem run permanecem `samples = 0`. | P0 |
| R-COL-9 | Invariantes de série pós-run: monotonicidade, sem duplicata, salto `|c_t − o_{t+1}| ≤ 8·ATR(14)`. Violação → run `suspect`, intervalo registrado. | P0 |
| R-COL-10 | Relatório final em stdout + `collect_runs` (JSON): velas novas, gaps in/out, payout amostrado, duração, status. | P0 |
| R-COL-11 | `--payout-only` para execuções horárias na VPS. | P1 |
| R-COL-12 | Cadência humana: 1 sessão, pausas 0,5–2 s entre chamadas, nunca > 2 logins/dia. | P0 |
| R-COL-13 | `status`: alerta se último run > 3 dias, se gaps in_session não resolvidos > 1% do período, se projeto Supabase pausado. | P1 |

## 5. Módulo `hub` (Supabase)

| ID | Requisito | P |
|---|---|---|
| R-HUB-1 | Migrations versionadas criando o schema da Arquitetura §3 (com `check`s). | P0 |
| R-HUB-2 | RLS: anon sem acesso a nada exceto `insert` em `live_outcomes` com `client_id` do JWT anônimo. Service key só no lab. | P0 |
| R-HUB-3 | Edge Function `publish`: verifica Ed25519 (A ou B), JSON Schema, `manifest_version > atual` (409 senão); grava `manifests/v{n}.json` e `current.json`; insere em `manifests`; chama `mirror`. | P0 |
| R-HUB-4 | Edge Function `outcomes`: lote ≤ 500, rate-limit 60 req/h por `client_id`, rejeita `ts` futuro ou > 7 dias. | P0 |
| R-HUB-5 | Edge Function `mirror`: copia `current.json` e `v{n}.json` para R2 (S3 API). Falha do espelho não falha o publish (log). | P1 |
| R-HUB-6 | Storage `manifests/` público de leitura, `Cache-Control: max-age=900`, ETag. | P0 |
| R-HUB-7 | `pg_cron` diário: exporta `candles` com `ts < now − 180d` para Parquet em `parquet/{asset}/{yyyymm}.parquet`, verifica contagem, apaga. | P1 |
| R-HUB-8 | Projeto de staging idêntico por migrations; suíte do lab só usa staging. | P0 |

## 6. Módulo `research`

| ID | Requisito | P |
|---|---|---|
| R-RES-1 | Recusa rodar com cobertura < 95% ou gaps in_session não resolvidos no período. | P0 |
| R-RES-2 | Holdout selado: últimos 3 meses removidos antes de tudo; hash gravado em `research_runs.holdout_range`; aberto uma vez no fim; queimado após uso. | P0 |
| R-RES-3 | Gramática: 1 Regime × 1 Trigger × 1 Confirm × TF ∈ {M1, M5, M15} × faixa horária × asset; parâmetros na grade de R-PRIM-5; nunca 2 da mesma categoria. Número total de candidatos registrado (usado no FDR). | P0 |
| R-RES-4 | Simulação fim-de-vela: decisão com vela `t` fechada; resultado = direção de `c_{t+1}` vs `c_t`; empate = perda; payout = `payouts(asset, floor(t, 1h))`; hora sem payout → operação excluída. | P0 |
| R-RES-5 | Replay: candidatos sobreviventes da triagem vetorizada são re-simulados alimentando o motor incremental de `primitives` vela a vela. Aprovação só via replay. | P0 |
| R-RES-6 | Penalidade de atraso: −0,5 pp (base) e −1,0 pp (pessimista). Aprovado só se passar na pessimista. | P0 |
| R-RES-7 | Portões em sequência: walk-forward ancorado 6m/2m → estabilidade (nenhuma janela < p_min; σ entre janelas < 3 pp) → FDR 5% BH + permutação 1.000× (percentil 99) → vizinhança ±15% (mediana passa) → PBO/CSCV 16 blocos < 20%. | P0 |
| R-RES-8 | Critério de aprovação: Wilson inferior 95% ≥ p_min + 1,5 pp com n ≥ 500 fora da amostra, em todos os portões e no holdout. | P0 |
| R-RES-9 | Scorer: `margem × √ops_por_dia`, pior sequência, resultado em 1.000 ops (stake 10, sem MG), `payout_min`. | P0 |
| R-RES-10 | Teste da moeda em CI: passeio aleatório com seed → zero aprovados. Oráculo reverso: lookahead injetado → p̂ ≈ 1. | P0 |
| R-RES-11 | Saída: `ranking.md` (humano), `candidates.json` (máquina), linha em `research_runs` com `data_hash`, `seed`, `primitives_version`. | P0 |
| R-RES-12 | Revalidação mensal inclui `live_outcomes` como amostra adicional fora da amostra. | P1 |

## 7. Módulo `publish`

| ID | Requisito | P |
|---|---|---|
| R-PUB-1 | Exige `--run-id`; monta manifesto a partir de `candidates.json` + curadoria (`--include/--exclude`). | P0 |
| R-PUB-2 | Preflight: valida schema, assinatura e vetores públicos do contrato de consumo gerados na etapa do `manifest_client`; é proibido importar código do bot. O bot executa os mesmos vetores em sua própria suíte. | P0 |
| R-PUB-3 | Diff contra manifesto vigente (adicionadas / removidas / alteradas); confirmação digitando o número de estratégias. Sem `--yes`. | P0 |
| R-PUB-4 | Chave privada A ou B em arquivo 0600 fora do repo; chave de teste distinta que o hub de produção recusa. | P0 |
| R-PUB-5 | Toda estratégia nova nasce `observation`. Promoção a `approved` só em publish posterior após SPRT ao vivo não rejeitar em 30 dias ou 200 ops. | P0 |

## 8. Bot (`trading-lab-desktop`)

| ID | Requisito | P |
|---|---|---|
| R-BOT-1 | `manifest_client`: polling 15 min com ETag; primário Supabase → espelho R2; regras fail-closed da Arquitetura §6, incluindo versão e hash de paridade. É o único ponto de integração com o Strategy Lab. | P0 |
| R-BOT-2 | Cache atômico (`tmp → fsync → rename`); assinatura verificada também na leitura do cache. | P0 |
| R-BOT-3 | Duas chaves públicas embutidas (A, B). Chave de teste não aceita em build de produção. | P0 |
| R-BOT-4 | Expiração comparada ao header `Date` do CDN; offline, tolerância 24 h. | P0 |
| R-BOT-5 | Catálogo dinâmico: 5 classes de família (F1–F5) instanciadas por `params` do manifesto; nova entrada = nova instância sem release. | P0 |
| R-BOT-6 | `payout_gate` antes de cada ordem: `wilson_lower ≥ 1/(1+payout_atual) + 0,015`; senão `PAYOUT_BELOW_VALIDATED_EDGE` com texto legível. | P0 |
| R-BOT-7 | `live_monitor` SPRT por `strategy_key` (H0 p = wilson_lower; H1 p = p_min; α = β = 0,05). Rejeição → `observation` + evento `strategy_demoted`. | P0 |
| R-BOT-8 | Estratégia `observation` só opera em Demo. Manifesto expirado sem substituto → todas `observation`. | P0 |
| R-BOT-9 | Estratégia removida sai após liquidar ordem em voo. | P0 |
| R-BOT-10 | `outcomes_uploader`: fila local SQLite, lote 5 min, fail-silent, nunca no ciclo de avaliação. Envia só `client_id`, `strategy_key`, `ts`, `won`, `payout_pct`. | P0 |
| R-BOT-11 | UI: lista de fichas (5 números), 3 estados, botão "Ligar", painel "Reprovadas — por quê" com uma frase. Nenhum número sem `n`; nenhuma taxa sem o mínimo ao lado. | P0 |
| R-BOT-12 | Gate de performance antigo reduzido a cooldown pós-loss; a catraca de break-even é removida em favor de R-BOT-6/7. | P0 |
| R-BOT-13 | Invariantes: 1 ordem em voo, sem `float` monetário, `time.monotonic()`, zero DB no ciclo, nenhum token fora do processo. | P0 |

## 9. Operação

| ID | Requisito | P |
|---|---|---|
| R-OPS-1 | `strategy-lab backup`: `pg_dump` criptografado para o PC; alerta se > 8 dias. | P0 |
| R-OPS-2 | CI executa os 5 testes: Moeda, Paridade, Canário (fixture), Manifesto hostil, DST + vela corrente. | P0 |
| R-OPS-3 | `RUNBOOK.md` com diário/semanal/mensal/incidentes e migração para VPS. | P1 |
| R-OPS-4 | Agendador do Windows configurado por script (`collect` diário 2×). | P1 |

## 10. Fora de escopo (v1)

- Dashboard web para o operador do lab.
- Coleta federada pelos clientes (`POST /ingest`) — reservado para v2.
- Qualquer estratégia fora da gramática (ML, previsão de dígito).
- Contas Real no lab: só coleta e Demo.

## 11. Métricas de aceite (end-to-end)

| Métrica | Alvo |
|---|---|
| `research` em série embaralhada | 0 aprovados |
| Build principal contém módulos/estado/dependências exclusivas do Strategy Lab | 0 |
| Imports cruzados, IPC ou banco compartilhado entre os produtos | 0 |
| Latência publish → bot | ≤ 15 min, sem restart |
| Operação do bot com rede fora | 45 dias |
| Detecção SPRT de p 3 pp abaixo | < 120 ops |
| Payout abaixo de `payout_min` | pausa em ≤ 1 ordem |
| Intervenção manual mensal | `collect` agendado + 1 `publish` |
