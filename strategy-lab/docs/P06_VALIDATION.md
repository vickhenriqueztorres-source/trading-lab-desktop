# P06 Validation — Hub Edge Functions

Data/hora local: 2026-09-02 21:17 BRT.

## Escopo

Implementação das Edge Functions do Hub Supabase para o Strategy Lab:

- `publish` — publicação de manifesto assinado e versionado;
- `outcomes` — ingestão de outcomes agregados por cliente anônimo;
- `mirror` — espelhamento idempotente de manifesto para R2/S3-compatible;
- `client_token` — emissão de JWT anônimo para `client_id` gerado pelo bot.

Nenhuma ordem foi enviada. Nenhuma conta de broker foi usada. Nenhum segredo real foi gravado em
arquivo, log ou teste.

## Arquivos principais

- `apps/hub/supabase/functions/_shared/canonical.ts`
- `apps/hub/supabase/functions/_shared/ed25519.ts`
- `apps/hub/supabase/functions/_shared/encoding.ts`
- `apps/hub/supabase/functions/_shared/hub.ts`
- `apps/hub/supabase/functions/_shared/jwt.ts`
- `apps/hub/supabase/functions/_shared/manifest_schema.ts`
- `apps/hub/supabase/functions/_shared/r2.ts`
- `apps/hub/supabase/functions/publish/index.ts`
- `apps/hub/supabase/functions/outcomes/index.ts`
- `apps/hub/supabase/functions/mirror/index.ts`
- `apps/hub/supabase/functions/client_token/index.ts`
- `apps/hub/supabase/migrations/0005_rate_limits.sql`
- `apps/hub/README.md`
- `apps/hub/supabase/functions/tests/hub_test.ts`

## Requisitos cobertos

| Requisito | Evidência local |
| --- | --- |
| R-HUB-3 `publish` | Testes Deno cobrem assinatura A, assinatura B, assinatura inválida, trust root de teste bloqueada em produção, versão regressiva 409, schema inválido 422, upload de `v14.json` e `current.json`, insert de manifesto e invoke do mirror. |
| R-HUB-4 `outcomes` | Testes Deno cobrem JWT anônimo, rejeição de timestamp futuro, rate limit 429, lote validado e `client_id` injetado a partir do token. |
| R-HUB-5 `mirror` | Teste Deno confirma cópia idempotente de `v14.json` e `current.json` para target fake; implementação real assina PUT S3-compatible para R2. |
| R-HUB-6 Storage | `apps/hub/README.md` documenta criação dos buckets `manifests` público e `parquet` privado, além de smoke de `ETag`/`Cache-Control`. |
| Cross-language canonicalization | Manifesto assinado em Python (`tests/fixtures/manifest_example.json`) verifica em Deno usando canonicalização compatível. |
| Rate limit persistente | Migration `0005_rate_limits.sql` adiciona tabela `rate_limits` e função `consume_rate_limit()`. |

## Validação executada

### Deno / TypeScript

Com Deno v2.9.6 local, instalado em `state/tools/deno-v2.9.6/` e verificado por checksum oficial:

```text
deno fmt --check
deno check publish/index.ts outcomes/index.ts mirror/index.ts client_token/index.ts archive/index.ts tests/*.ts
deno lint
deno test --allow-read=../../../../tests/fixtures,../../../../tests/keys
```

Resultado:

```text
12 passed, 0 failed
```

### Python / Strategy Lab

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall packages tools
git diff --check
```

Resultado:

```text
257 passed, 3 skipped
Ruff check: passed
Ruff format --check: passed
mypy strict: passed
compileall: passed
git diff --check: passed
```

Os 3 skips são testes staging de P05 que exigem `SUPABASE_STAGING_DB_URL`.

## Bloqueios externos

Não foi executado o smoke real `supabase functions serve` + `curl` porque Docker/Podman não está
instalado no PATH da máquina:

```text
docker: command not found
podman: command not found
```

Também não houve deploy remoto nem criação remota dos buckets, porque não há `SUPABASE_ACCESS_TOKEN`
e `SUPABASE_STAGING_DB_URL` configurados no ambiente. As chaves coladas em conversa não foram
persistidas nem reutilizadas em arquivos.

## Veredito

P06 validada localmente com fakes determinísticos, typecheck Deno estrito e suíte Python verde.
Validação Supabase runtime/remota permanece pendente de Docker/Podman local ou credenciais staging
configuradas fora do repositório.
