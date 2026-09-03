# Strategy Lab — Arquitetura (v2, Supabase)

Catalogador de estratégias para opções binárias na IQ Option: coleta dados, descobre e valida
estratégias com rigor estatístico, publica um manifesto assinado, e o bot do cliente
(`trading-lab-desktop`) consome só o que sobreviveu. Esta versão incorpora as mitigações dos
11 pontos de falha mapeados (ver §9).

Decisões fixas:

- O **Strategy Lab é um subprojeto autônomo**, com processo, ambiente Python, configuração,
  persistência, agendamento, testes e build próprios. Ele não é empacotado no EXE principal.
- A única integração operacional com `trading-lab-desktop` é o **manifesto JSON assinado e
  versionado**, obtido por HTTPS e mantido em cache. Não existe import cruzado, IPC, banco
  compartilhado ou leitura direta de arquivos entre os dois aplicativos.
- Backend único: **Supabase** (Postgres + Storage/CDN + Edge Functions + `pg_cron` + RLS).
- Espelho de storage (gratuito, só leitura): **Cloudflare R2** para manifesto e Parquet frio.
- Conexão com a IQ Option: **`iqoptionapi` vendorizado** (cópia no repo, diff revisado a cada update).
- Coletor roda **no seu PC, 1×/dia** (2×/dia se possível para densidade de payout). Migração
  futura para VPS = mesmo comando em `cron`, zero mudança de código.
- Nenhum processo seu fica vivo 24h. O único componente contínuo (Supabase) é gerenciado.

---

## 1. Visão geral

```text
┌──────────────── SEU PC (1–2×/dia, agendado) ───────────────────────────────────────┐
│ strategy-lab                                                                        │
│  collect  → canário → login IQ Option (iqoptionapi vendorizado) → backfill M1 +     │
│             payout → invariantes → UPSERT Supabase → relatório                      │
│  research → lê Supabase/Parquet (DuckDB) → gramática → replay → portões → holdout   │
│             selado → ranking.md                                                     │
│  publish  → valida schema → simula carga como o bot → diff → assina Ed25519 →       │
│             Edge Function /publish                                                  │
└─────────────────────────────────────────────────────────────────────────────────────┘
          │ escreve (service key)                          │ publica (assinatura)
          ▼                                                ▼
┌──────────── SUPABASE ─────────────────────────────────────────────────────────────┐
│ Postgres: candles · payouts · gaps · market_sessions · manifests · research_runs · │
│           live_outcomes   (numeric, UTC epoch, RLS)                                │
│ Storage (CDN): manifests/current.json (+ versões) · parquet/ (frio)                │
│ Edge Functions: publish · outcomes                                                 │
│ pg_cron: arquivar candles > 180d → Parquet · agregar live_outcomes                 │
└───────────────────────────────────────────────────────────────────────────────────┘
          │ espelho automático (Edge Function após publish)
          ▼
┌──────────── CLOUDFLARE R2 (espelho, egress zero) ───────┐
│ manifests/current.json · parquet/                        │
└──────────────────────────────────────────────────────────┘
          │ GET a cada 15 min (ETag; primário → espelho)
          ▼
┌──────────── PC DO CLIENTE (trading-lab-desktop) ─────────────────────────────────┐
│ manifest_client → assinatura (2 chaves públicas) → primitives_version/hash → schema │
│                   expiração (relógio do CDN) → cache atômico                      │
│ runtime local compatível (sem importar o lab) → famílias F1–F5 por params         │
│ payout_gate → break-even ao vivo por ordem                                        │
│ live_monitor (SPRT) → rebaixa para "Em observação" → outcomes_uploader (fila)     │
│ UI → ficha de 5 números, três estados, só do manifesto                            │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Princípios:

1. **O manifesto é o único contrato** entre lab e bot.
2. **Estado mora no banco.** Qualquer job pode morrer e rodar de novo (idempotência).
3. **Fail-closed em tudo que é controlável; detecção rápida + degradação segura no que não é.**
4. **Nenhum caminho de operação do bot passa pela rede sua.** Rede fora = bot segue com cache.

## 2. Isolamento e layout real

O código fica no mesmo repositório Git por conveniência de revisão, mas forma dois produtos
independentes. O diretório `strategy-lab/` possui seu próprio `pyproject.toml`, lock de dependências,
`.venv`, diretório de estado e pipeline de build. Nenhum módulo de produção do laboratório pode
importar `apps/`, `packages/` ou estado privado do aplicativo principal, e o aplicativo principal
não pode importar módulos de `strategy-lab/`.

Compatibilidade numérica não depende de import cruzado: o laboratório publica no manifesto
`primitives_version` e `primitives_parity_sha256`. O bot contém sua implementação compatível e
recusa o manifesto se versão ou hash diferirem dos valores incorporados no seu build. As duas
implementações são verificadas contra o mesmo vetor público de conformidade de 10.000 velas.

```text
trading-lab-desktop/
├── apps/                              # produto principal existente
│   └── core/
│       ├── manifest_client.py         # único ponto de entrada vindo do Strategy Lab
│       ├── payout_gate.py
│       ├── live_monitor.py
│       └── outcomes_uploader.py
├── packages/                          # domínio privado do produto principal
└── strategy-lab/                      # subprojeto autônomo; não entra no EXE principal
    ├── pyproject.toml
    ├── uv.lock                        # ou lock equivalente, exclusivo do laboratório
    ├── apps/hub/supabase/
    │   ├── migrations/
    │   └── functions/{publish,outcomes,mirror}/
    ├── packages/
    │   ├── primitives/                # implementação de referência do laboratório
    │   │   ├── regime/
    │   │   ├── trigger/
    │   │   ├── confirm/
    │   │   ├── VERSION
    │   │   └── tests/parity/
    │   └── manifest_schema/
    ├── vendor/iqoptionapi/
    ├── tools/strategy_lab/
    │   ├── collect/
    │   ├── research/
    │   ├── publish/
    │   └── cli.py
    ├── tests/
    ├── state/                         # local, ignorado pelo Git
    └── dist/                          # build próprio, ignorado pelo Git
```

## 3. Banco de dados (Supabase / Postgres)

```sql
-- velas M1 fechadas. ts = epoch em segundos, múltiplo de 60, UTC. NUNCA a vela corrente.
create table candles (
  asset        text        not null,            -- 'EURUSD' | 'EURUSD-OTC' (séries distintas)
  ts           bigint      not null,
  o numeric(18,8) not null, h numeric(18,8) not null,
  l numeric(18,8) not null, c numeric(18,8) not null,
  tick_vol     integer     not null default 0,
  source       text        not null,            -- 'iqoptionapi@<vendor_commit>' | 'client:<uuid>'
  collected_at bigint      not null,
  primary key (asset, ts),
  check (l <= least(o,c) and greatest(o,c) <= h),
  check (ts % 60 = 0)
);

-- payout observado por hora. samples=0 marca hora sem amostra (pesquisa SABE que não sabe).
create table payouts (
  asset text not null, hour_ts bigint not null,
  payout_pct numeric(5,2), samples integer not null default 0,
  primary key (asset, hour_ts), check (hour_ts % 3600 = 0)
);

create table market_sessions (
  asset text not null, weekday smallint not null,  -- 0=seg..6=dom
  open_min smallint not null, close_min smallint not null,   -- minutos UTC do dia
  primary key (asset, weekday, open_min)
);

create table gaps (
  asset text, from_ts bigint, to_ts bigint, detected_at bigint,
  in_session boolean not null, resolved boolean not null default false,
  primary key (asset, from_ts)
);

create table research_runs (
  run_id text primary key, started_at bigint, finished_at bigint,
  data_hash text, primitives_version text, seed bigint,
  candidates integer, approved integer, holdout_range text, coverage_pct numeric(5,2),
  status text check (status in ('ok','suspect','aborted'))
);

create table manifests (
  manifest_version integer primary key,
  published_at bigint, expires_at bigint,
  storage_path text, sha256 text, signature text,
  primitives_version text, research_run_id text references research_runs(run_id),
  key_id text not null                                  -- 'A' | 'B'
);

create table live_outcomes (
  client_id uuid not null, strategy_key text not null, ts bigint not null,
  won boolean not null, payout_pct numeric(5,2) not null,
  primary key (client_id, strategy_key, ts)
);
```

Regras:

- `numeric`, nunca `float`. Tempo em epoch inteiro UTC; `timestamptz` só em views de apresentação.
- Retenção quente: 180 dias. `pg_cron` diário exporta o excedente para Parquet no Storage
  (e o espelho para R2), depois apaga. Histórico frio também fica no seu PC (`backup`).
- **RLS**: `live_outcomes` — anon só `insert` com `client_id = auth.jwt()->>'client_id'` (JWT
  anônimo emitido no primeiro contato) e rate-limit na Edge Function. Todas as outras tabelas:
  sem acesso anon; `collect`/`research`/`publish` usam service key (só no seu PC/VPS).
- **Staging**: segundo projeto Supabase free, schema idêntico por migrations. A suíte do lab
  usa staging; produção nunca é tocada por teste.
- **Backup**: `strategy-lab backup` (`pg_dump` semanal para o seu PC, criptografado). O free
  tier não tem backup — isso é obrigatório no runbook.
- **Pausa por inatividade (7 dias)**: o `collect` diário mantém o projeto acordado; `status`
  alerta se o último run tem > 3 dias.

## 4. `strategy-lab collect` — job diário idempotente

```text
0. preflight: relógio do PC vs. NTP (|Δ| < 5s, senão aborta); credenciais no keyring do SO
1. CANÁRIO: busca 5 velas fixas de data conhecida → compara com fixture. Diferente → ABORTA
   antes de escrever qualquer coisa (detecta mudança de formato da API em segundos)
2. login (1 sessão, pausas humanas entre chamadas)
3. para cada asset (pares reais + '-OTC'):
     watermark = max(ts) em candles
     loop get_candles(asset, 60, 1000, end) até watermark
     filtro: ts < floor(now,60) - 60   (vela corrente NUNCA é gravada)
     validação por vela (pydantic estrito + checks do banco). 1 inválida → run abortado
     UPSERT em lote
     gaps: comparar grade esperada vs. recebida; classificar in_session via market_sessions
4. payout_sampler: payout atual → payouts(hour_ts corrente, samples+1); horas sem run
   permanecem samples=0
5. invariantes de série: ts monotônico, sem duplicata, |c_t - o_{t+1}| ≤ k·ATR (salto
   absurdo → run 'suspect', intervalo marcado)
6. logout. Relatório: velas novas, gaps (in/out session), duração, status, próximo watermark
```

Propriedades: stateless, idempotente (3 runs = 1 run), recupera qualquer downtime (API guarda
semanas). Única perda em dias sem rodar: payout daquelas horas — registrado como `samples=0`.

Adaptador `iq_client.py`: única classe que importa `vendor/iqoptionapi`. Interface própria
(`fetch_candles`, `fetch_payout`, `login`, `logout`). Quando a API mudar, troca-se uma classe.

### 4.1 P03 — fronteira de coleta implementada

Fork fixado: victalejo/iqoptionapi@acac6e08333466ae188c7dfa7fd2a03174e34ca2, licença MIT.
Após autorização do operador, três patches mínimos estão em vendor/iqoptionapi/PATCHES.md
(TLS, logging, relógio), com hashes antes/depois e diff. O lock fica em vendor/REQUIREMENTS.txt.

IQClient usa construtores low-level do vendor só em iq_client.py e fronteiras
de transporte com allowlists de leitura. Sem negociação, saldo, troca de conta,
connect legado, retry ou reconexão automáticos. Autenticação deve ser confirmada;
HTTPS/WS verificam TLS e redirects de login são recusados.
Falha/challenge exige intervenção, sem trocar endpoint. JSON de preço é decodificado
em Decimal. Payout M1 usa (100 - commission) / 100 do catálogo turbo; ausência é None.
Nomes mantêm -OTC; IDs vêm do catálogo, sem tabela fixa presumida.

Uma instância serializa chamadas, com pausa injetável de 0,5–2s também entre catálogo
e velas. Encerramento local não espera pacing. Sem escrita de banco. record-fixture
exige lote íntegro e cobertura exata antes da gravação exclusiva. Remove explicitamente
metadados não-preço; erros de vela retêm só campos numéricos seguros. Sem vela corrente.

O CLI requer checkout independente e instalação editável do Lab; empacotamento standalone
é futuro. Nenhum build/import no EXE principal. Coleta real de 1.000 velas continua
manual e não executada; testes usam fakes. P04 deverá implementar preflight NTP, canário,
cota diária persistente de login, backfill e Supabase: não estão entregues pelo P03.

## 5. `strategy-lab research` — mensal

```text
0. cobertura: recusa rodar se cobertura de velas < 95% no período ou gaps in_session não
   resolvidos; relatório de cobertura antes de qualquer cálculo
1. HOLDOUT SELADO: últimos 3 meses removidos e lacrados (hash registrado)
2. grammar: candidatos = 1 Regime × 1 Gatilho × 1 Confirmação × TF × faixa horária × asset
   (nunca 2 primitivos da mesma categoria; ~5.000/rodada, número usado no FDR)
3. vector_scan (NumPy/Polars): triagem grosseira, descarta o que não chega perto do break-even
4. replay_simulator: sobreviventes re-simulados alimentando O MESMO motor incremental do bot,
   vela a vela — decisão em t só vê [..t]; aposta em t+1; payout(asset, floor(t, 1h));
   hora sem payout → operação EXCLUÍDA; penalidade de atraso −0,5pp e versão pessimista −1pp
5. gates: walk-forward ancorado (6m/2m) → estabilidade (nenhuma janela < p_min; σ < 3pp)
   → FDR 5% (BH) + permutação 1.000× (percentil 99) → vizinhança ±15% (mediana passa)
   → PBO/CSCV < 20%
6. holdout: aprovados abertos UMA vez; reprovado = descartado; holdout queimado para a próxima
7. scorer: margem × √freq, pior sequência, resultado em 1.000 ops (stake 10), payout_min
8. SANIDADE: mesma rodada em série embaralhada / passeio aleatório DEVE aprovar zero
9. saída: ranking.md (para você) + candidates.json + research_runs
```

## 6. Manifesto — o contrato

```json
{
  "expires_at": 1792238400,
  "key_id": "A",
  "manifest_version": 14,
  "primitives_parity_sha256": "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10",
  "primitives_version": "1.0.0",
  "published_at": 1788350400,
  "research_run_id": "run_2026_09",
  "schema_version": 1,
  "signature": "ed25519:eY0RTJJxiecx418FRNgtPpks02F7p5KLxuNjFN6Bql5BM9i3lT0Md6n1OOlMVyL1KkXd5c3oY/ovwHA27RAfDg==",
  "strategies": [
    {
      "asset": "EURUSD",
      "display_name_pt": "Reversão de Extremo",
      "family": "F1",
      "hours_utc": [
        0,
        6
      ],
      "key": "f1_reversal:EURUSD:M1:00-06",
      "management": {
        "martingale_steps_max": 2,
        "paroli": true,
        "stake_pct": "1.0"
      },
      "params": {
        "adx_len": "14",
        "adx_max": "20",
        "bb_k": "2.0",
        "bb_len": "20",
        "rsi_hi": "80",
        "rsi_len": "7",
        "rsi_lo": "20"
      },
      "status": "observation",
      "timeframe": "M1",
      "validated": {
        "holdout_passed": true,
        "n": 1240,
        "ops_per_day": "11.2",
        "p_hat": "0.578",
        "p_min_at_validation": "0.541",
        "payout_min": "0.84",
        "result_1000_ops_stake10": "182.00",
        "wilson_lower": "0.561",
        "windows_passed": "8/8",
        "worst_streak": 6
      }
    }
  ]
}
```

### 6.1 Formalização P02 — contrato v1 (R-MAN-1..7)

O exemplo acima é idêntico a `tests/fixtures/manifest_example.json`: assinatura com a
chave de TESTE pública, rejeitada por produção. Não é um manifesto publicado nem prova de edge.
O valor de paridade é o real do P01, prefixado por `sha256:`.

Tipos: dinheiro, probabilidades, payout, stake percentual e **todos os valores de params**
são strings decimais ASCII (`^-?[0-9]+(\\.[0-9]+)?$`), até 24 caracteres. Não há coerção:
`"20"` é permitido; `20`, `20.0`, `"2e1"`, NaN e Infinity não são parâmetros válidos.
Versões numéricas do schema/manifesto, epochs, horas, contagens (`n`, `worst_streak`,
`martingale_steps_max`) continuam inteiros estruturais; flags continuam booleanos.
`primitives_version` é semver e `primitives_parity_sha256` é `sha256:` + 64 hex minúsculos.

Todas as famílias exigem explicitamente todos os seus parâmetros; nada é preenchido silenciosamente.
Os nomes existentes são preservados. A fonte de cada faixa de construtor é
`manifest_schema.families.FAMILY_BINDINGS` → `primitives.REGISTRY[name].param_spec[param]`.

| Família | Regime | Trigger | Confirm | Parâmetros publicados |
|---|---|---|---|---|
| F1 Reversal | adx | bb_close_outside | rsi_extreme | adx_len, adx_max, bb_len, bb_k, rsi_len, rsi_lo, rsi_hi |
| F2 Pullback | ema_alignment | ema_pullback | candle_rejection | ema_short, ema_medium, ema_long, pullback_len, pullback_tolerance, body_max, wick_min |
| F3 LevelRejection | session_window | level_touch | candle_rejection | level_support, level_resistance, level_tolerance, body_max, wick_min |
| F4 SqueezeBreak | bb_width_ratio | range_break | tick_volume_ratio | bb_len, bb_k, width_median_len, width_ratio_max, break_len, volume_len, volume_min |
| F5 Quadrant | session_window | quadrant_majority | rsi_extreme | quadrant_window, rsi_len, rsi_lo, rsi_hi |

Mapeamento exato: `*_len` aponta ao period/length correspondente; `rsi_lo/hi` a lower/upper;
`body_max/wick_min` a max_body_ratio/min_wick_ratio; `volume_min` a minimum_ratio.
O arquivo `families.py` e o schema exportado enumeram todos os aliases sem inferência.
Em F3/F5, session_window recebe `hours_utc × 60`; horas são [início,fim), 0 ≤ início < fim ≤ 24.
Sessões que atravessam meia-noite exigem entradas distintas, não uma faixa ambígua.
Em F3 os níveis são injetados explicitamente, não inferidos pelo contrato.

Dois **gates de composição**, não parâmetros do construtor do indicador, têm faixas próprias:
`adx_max` = 0..100, passo 1 (ADX ≤ máximo); `width_ratio_max` = 0.1..1, passo 0.1.
Não foram adicionados argumentos nem alterados cálculos do P01. EMA exige short < medium < long;
níveis exigem support < resistance; RSI exige lower < upper. A grade usa (valor − mínimo) / passo
inteiro, com aritmética exata; kind int exige valor integral mesmo quando publicado como string.

`payout_min` é o **menor valor seguro na grade 0.01**, arredondado para cima, em (0,1].
Para Wilson 0.561, a fronteira contínua é aproximadamente 0.831502 e o mínimo publicado é 0.84.
O valor tem de satisfazer Wilson ≥ 1/(1+payout_min)+0.015 e o passo anterior deve falhar.
Esta validação de consistência não comprova a pesquisa nem recalcula Wilson a partir de n.
Reprovadas também exigem métricas válidas; `reason_pt` não pode ser vazio.
Approved exige holdout_passed=true; a quarentena e a evidência de promoção pertencem ao publish.

`management` contém uma recomendação declarativa: 0 < stake_pct ≤ 100; 0..10 etapas máximas;
paroli booleano. Não habilita Martingale, não configura a conta e nunca substitui limites do Core.
Epochs/contagens são limitados a inteiros JSON seguros até 2^53−1. Manifesto: até 5.000 entradas,
4 MiB de JSON, 32 níveis de aninhamento, chaves de estratégia únicas e validade 0 < duração ≤ 45d.

### 6.2 Validação em camadas e portabilidade

1. Leitura JSON limitada: rejeitar duplicatas (inclusive aninhadas), floats/exponentes numéricos,
   NaN/Infinity, Unicode inválido e inteiros além de 2^53−1 **antes** da desserialização coerciva.
2. JSON Schema Draft 2020-12 para estrutura + perfil obrigatório
   `urn:strategy-lab:manifest-policy:v1` para comparação entre campos e decimais.
3. Ed25519 sobre UTF-8, chaves ordenadas como Python (pontos de código Unicode), sem espaços,
   ensure_ascii=false; excluir somente signature na raiz. Não normalizar strings, casas decimais
   ou campos opcionais. Omitido e null são conteúdos assinados distintos.
4. Verificar trust store A/B, proibir chave pública de teste em produção e conferir versão/hash
   contra o consumidor. Expiração corrente/cache/antirrollback dependem do consumidor (P09).

JSON Schema padrão não compara duas propriedades nem impõe minimum/multipleOf a uma string.
Por isso o schema exportado marca `x-tl-policy-v1` como requisito de aceitação, usa
`x-tl-decimal-range` e `x-tl-ordered-params`, e **não pode ser usado sozinho para aprovar**.
O adaptador de teste jsonschema executa essas regras independentemente de Pydantic.
O hub Deno e o bot devem implementar o perfil independentemente e passar os mesmos vetores
públicos antes de uso operacional; ausência do perfil deve abortar o startup/preflight.
Não há Edge Function nem deploy Supabase no P02. `contracts/README.md` descreve o perfil.

Regras do bot (todas fail-closed):

| Condição | Ação |
|---|---|
| assinatura inválida para A e B | descarta, mantém anterior, `manifest_rejected` |
| `primitives_version` ≠ instalada | descarta, evento |
| `primitives_parity_sha256` ≠ hash incorporado no bot | descarta, evento |
| `params` fora das faixas do JSON Schema | descarta manifesto inteiro |
| `manifest_version` ≤ cache | descarta |
| rede fora | usa cache até `expires_at` |
| `expires_at` | comparado ao header `Date` do CDN; offline, tolerância 24h |
| expirado sem substituto | estratégias → "Em observação" (Demo only), aviso na UI |
| estratégia removida no novo | sai após liquidar ordem em voo |
| cache truncado/corrompido | descartado (assinatura verificada também na leitura) |

Cache: `manifest.tmp` → `fsync` → `rename`. Chaves: bot nasce com **duas** públicas (A ativa,
B reserva). Chave privada de teste é distinta e o bot de produção não a aceita.

## 7. Hub Supabase — três funções, nada mais

- **`GET manifests/current.json`** — objeto público no Storage (CDN, `ETag`, `Cache-Control:
  max-age=900`). Espelho em R2 com o mesmo caminho; o bot tenta primário → espelho.
- **`POST /functions/v1/publish`** — verifica Ed25519 (A ou B) do corpo, JSON Schema, e
  `manifest_version > atual` (senão 409). Grava `manifests/v{n}.json`, atualiza `current.json`,
  insere em `manifests`, dispara `mirror` para R2. Sem senha: a assinatura é a autenticação.
- **`POST /functions/v1/outcomes`** — W/L anônimo em lote; JWT anônimo por `client_id`;
  rate-limit; insere em `live_outcomes`. Alimenta o monitor e a revalidação mensal.

Sem painel, sem login de usuário. Dashboard para você, se quiser, é uma página à parte lendo
o Postgres.

## 8. Dentro do bot

1. `manifest_client` — polling 15 min com regras da §6.
2. Catálogo dinâmico: famílias F1–F5 são código (5 classes); cada entrada do manifesto é uma
   instância com `params` injetados. Novas oportunidades chegam **sem release**.
3. `payout_gate` — antes de cada ordem: `wilson_lower ≥ 1/(1+payout_atual) + margem_min`,
   senão `PAYOUT_BELOW_VALIDATED_EDGE` (visível na ficha: "Opera com payout ≥ 84%. Agora: 82%").
4. `live_monitor` — SPRT por `strategy_key` (H0: p = wilson_lower; H1: p = p_min; α=β=0,05).
   Rejeição → "Em observação" + evento. Substitui a catraca do gate de performance; o gate
   antigo vira só cooldown pós-loss.
5. Quarentena: toda estratégia nova nasce `observation` por 30 dias ou 200 ops em Demo; só
   `approved` após o SPRT não rejeitar.
6. `outcomes_uploader` — fila local, lote 5 min, fail-silent, nunca bloqueia o ciclo.
7. UI — ficha de 5 números, três estados (Aprovada / Em observação / Reprovada com uma frase).

Invariantes preservadas: 1 ordem em voo, sem `float` monetário, `time.monotonic()`, zero
leitura de banco no ciclo (manifesto em memória), nenhum token fora do processo.

## 9. Matriz de falhas → controle

| # | Falha | Controle (onde) |
|---|---|---|
| 1 | API não oficial quebra | adaptador isolado; canário aborta; validação por vela; vendor pinado; backfill recupera; `status` alerta em 1 dia (collect) |
| 2 | Lookahead/defeito do simulador | replay com motor incremental (impossível por construção); teste da moeda em cada commit; oráculo reverso; payout da hora de decisão; atraso −0,5/−1pp (research) |
| 3 | Indicador lab ≠ bot | implementações isoladas; vetor público de conformidade; versão + paridade SHA-256 no manifesto; `Decimal` com precisão fixa; recusa pelo `manifest_client` |
| 4 | Payout muda | `payout_gate` ao vivo por ordem; `payout_min` no manifesto; payout por (asset, hora, weekday) na pesquisa |
| 5 | Dados corrompidos / DST / vela corrente | UTC epoch; vela corrente nunca gravada; `market_sessions`; invariantes por vela e série; `source`+`collected_at` para re-backfill |
| 6 | Manifesto (chave, versão, cache, params) | 2 chaves públicas; chave de teste distinta; `Date` do CDN; cache atômico; 409 em regressão; schema com faixas em publish e no bot; preflight simula o bot |
| 7 | Supabase indisponível | bot nunca depende de rede; cache 45 dias; fila de outcomes; espelho R2; collect/publish são refazíveis |
| 8 | Overfitting sobrevivente | FDR sobre o nº real de candidatos; holdout selado e queimado; quarentena Demo 30d/200 ops |
| 9 | Regime muda | SPRT detecta em ~60–120 ops; stop diário acota a perda; revalidação mensal usa `live_outcomes` |
| 10 | Erro humano | `publish` com diff e confirmação digitada; `research` recusa cobertura < 95%; chave de teste ≠ prod; `pg_dump` semanal; runs reproduzíveis (hash, seed, versão) |
| 11 | Conta IQ Option bloqueada | conta de coleta ≠ conta de operação; 1–2 logins/dia de IP residencial; fonte secundária (Dukascopy) para pares reais; aviso contratual ao cliente |

Residual não zerável: 1 (terceiro), 9 (mercado), 11 (corretora), e o falso positivo
estatístico de 8 (quantificado: FDR 5% × holdout × quarentena → bem abaixo de 1% em Real).

## 10. Segurança

| Ativo | Onde | Quem |
|---|---|---|
| Credenciais IQ Option (coleta) | keyring do PC → env na VPS | só `collect` |
| Chave privada Ed25519 A e B | PC, 0600, backup offline em 2 lugares | só `publish` |
| Chave privada de teste | repo de testes (pública por design) | suíte |
| Service key Supabase | PC/VPS env | lab |
| Anon key + JWT anônimo | bot | só insert em `live_outcomes` |
| Chaves públicas A e B | embutidas no bot | todos |
| Dados do cliente | `client_id` UUID + W/L; nunca saldo, token, loginid | hub |

## 11. Testes — isolados por camada

| Camada | Como | IQ Option? | Bot? |
|---|---|---|---|
| collect | fixtures gravadas de `get_candles`; projeto Supabase staging | não | não |
| primitives | mesmo vetor público de 10.000 velas; hashes lab ↔ bot idênticos | não | sim (implementação local isolada) |
| research | moeda (zero aprovados); oráculo reverso; sintéticos com edge conhecido | não | não |
| hub | assinatura A/B/inválida; 409; schema; ETag; RLS de outcomes | não | não |
| manifest_client | servidor HTTP fake; manifestos hostis; DST; cache truncado | não | sim |

Os cinco testes de CI obrigatórios: **Moeda, Paridade, Canário, Manifesto hostil, DST + vela
corrente.**

## 12. Runbook

- **Diário (~2 min)**: `strategy-lab collect` (Agendador do Windows). Ler relatório.
- **Semanal**: `strategy-lab backup`.
- **Mensal (~1h CPU, 15 min seus)**: `research` → revisar `ranking.md` → `publish` → confirmar
  no bot Demo que o manifesto chegou.
- **Quando necessário**: alerta do `status`/SPRT; novo primitivo (release + bump); rotação de
  chave (planejada); update do vendor (diff revisado + canário).
- **Migração para VPS**: clone, install, env, dois crons (`collect` diário, `collect
  --payout-only` horário). Nada mais.

## 13. Ordem de construção

1. Strategy Lab: `primitives` + `manifest_schema` (contrato e vetores de conformidade)
2. Strategy Lab: `vendor/iqoptionapi` + `collect` + migrations Supabase + staging
3. Hub: Edge Functions `publish`, `outcomes`, `mirror`; `pg_cron` de arquivamento
4. Strategy Lab: `research` com F1–F5 fixas (sem gramática) → primeiro veredito
5. Bot: `manifest_client` isolado e testes dos vetores de conformidade
6. Strategy Lab: `publish` com preflight pelo contrato já implementado no passo 5
7. Bot: catálogo dinâmico, `payout_gate`, `live_monitor`, uploader e UI
8. Strategy Lab: `grammar` + `scorer` + holdout → descoberta de oportunidades
9. Hardening final: CI com os 5 testes, runbook, VPS e builds independentes
