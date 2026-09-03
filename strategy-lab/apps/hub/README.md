# Strategy Lab Hub

Hub Supabase do Strategy Lab. Ele recebe manifestos assinados, outcomes agregados dos bots e
espelha o manifesto público para R2. O hub não executa ordens e não armazena credenciais de broker.

## Edge Functions

- `publish`: publica um manifesto assinado Ed25519, valida schema e impede regressão de versão.
- `outcomes`: recebe outcomes agregados com JWT anônimo contendo `client_id`.
- `mirror`: copia `manifests/v{n}.json` e `manifests/current.json` para R2/S3-compatible.
- `client_token`: emite JWT anônimo de 1 ano para um UUID gerado pelo bot.
- `archive`: stub chamado pela rotina de arquivamento de candles antigos.

## Storage

Criar os buckets:

```powershell
supabase storage create manifests --public
supabase storage create parquet --private
```

O bucket `manifests` deve ser público para leitura do bot. O bucket `parquet` permanece privado e
é usado para arquivo operacional. Os objetos do manifesto usam cache de 900 segundos:

- `manifests/v{manifest_version}.json`
- `manifests/current.json`

Smoke esperado depois de publicar:

```powershell
curl -I "$env:SUPABASE_URL/storage/v1/object/public/manifests/current.json"
```

A resposta deve conter `ETag` e `Cache-Control`.

## Migrations

Aplicar em staging primeiro:

```powershell
.\scripts\supabase_staging.ps1
```

Ou via CLI:

```powershell
supabase link --project-ref <staging-ref>
supabase db push
```

Nunca aplicar migrations com uma URL que contenha o ref de produção quando
`SUPABASE_PROD_REF` estiver definido.

## Secrets das functions

Configurar no Supabase, sem commitar valores:

```powershell
supabase secrets set HUB_ENV=staging
supabase secrets set MANIFEST_PUBKEY_A=<hex-public-key-a>
supabase secrets set MANIFEST_PUBKEY_B=<hex-public-key-b>
supabase secrets set HUB_JWT_SECRET=<random-hs256-secret>
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
supabase secrets set R2_ENDPOINT=<r2-endpoint>
supabase secrets set R2_BUCKET=<r2-bucket>
supabase secrets set R2_ACCESS_KEY_ID=<r2-access-key-id>
supabase secrets set R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
supabase secrets set R2_REGION=auto
```

`MANIFEST_TEST_PUBKEY` só pode ser configurada em staging (`HUB_ENV=staging`). Em produção, a
function rejeita a trust root de teste.

## Smoke local

Servir functions localmente:

```powershell
supabase functions serve publish --env-file .env.local
```

Publicar fixture assinada:

```powershell
curl -i -X POST "http://127.0.0.1:54321/functions/v1/publish" `
  -H "content-type: application/json" `
  --data-binary "@tests/fixtures/manifest_example.json"
```

Resultado esperado:

- primeira submissão: `201` com `sha256`;
- reenvio da mesma versão: `409`.

## Testes Deno

```powershell
cd apps/hub/supabase/functions
deno fmt --check
deno lint
deno check publish/index.ts outcomes/index.ts mirror/index.ts client_token/index.ts archive/index.ts tests/*.ts
deno test --allow-read=../../../../tests/fixtures,../../../../tests/keys
```
