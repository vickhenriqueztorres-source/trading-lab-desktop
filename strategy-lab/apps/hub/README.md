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

Em 2026-09-05, o smoke do projeto staging confirmou `ETag`, mas o endpoint público hospedado
respondeu `Cache-Control: no-cache` mesmo com o upload enviando o contrato oficial
`cache-control: max-age=900`. Esta é uma limitação conhecida do Storage hospedado
([supabase/storage#1290](https://github.com/supabase/storage/issues/1290)). O metadado solicitado
deve continuar sendo gravado, o smoke não pode declarar o TTL aprovado enquanto a resposta seguir
`no-cache`, e o espelho R2 é a rota de cache independente prevista pela arquitetura.

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

Script seguro do repositório, usando apenas variáveis de ambiente e a CLI pinada:

```powershell
$env:SUPABASE_STAGING_DB_URL = "<postgres-url-percent-encoded>" # opcional se o Hub estiver vinculado
$env:SUPABASE_PROJECT_REF = "<staging-ref>"                      # opcional após `supabase link`
$env:SUPABASE_ACCESS_TOKEN = "<supabase-management-access-token>" # opcional após `supabase login`
$env:SUPABASE_PROD_REF = "<production-ref-to-refuse>"

# Opcional, para Edge Functions:
$env:HUB_ENV = "staging"
$env:MANIFEST_PUBKEY_A = "<hex-public-key-a>"
$env:MANIFEST_PUBKEY_B = "<hex-public-key-b>"
$env:HUB_JWT_SECRET = "<random-hs256-secret>"
$env:ARCHIVE_CONTROL_TOKEN = "<random-control-secret>"
$env:SUPABASE_SERVICE_ROLE_KEY = "<service-role-key>"

.\scripts\supabase_apply_remote.ps1
```

`SUPABASE_STAGING_DB_URL` deve começar com `postgres://` ou `postgresql://`; a URL HTTPS
`https://<ref>.supabase.co` é a API REST e não é uma conexão PostgreSQL. Depois de executar
`supabase login` e `supabase link --workdir apps/hub --project-ref <staging-ref>`, o script lê o
ref vinculado e pode operar sem repetir o token ou a senha na linha de comando.

O script aplica, nesta ordem:

1. preflight de `pg_net`;
2. `db push` das migrations;
3. criação idempotente dos buckets `manifests` e `parquet`;
4. secrets das Edge Functions, via arquivo temporário em `state/` removido no final;
5. deploy das Edge Functions `archive`, `client_token`, `manifest_current`, `mirror`, `outcomes` e
   `publish`.

`archive`, `mirror` e `publish` preservam a validação JWT do gateway. `client_token` e
`outcomes` são implantadas com `--no-verify-jwt`: a primeira emite a identidade anônima inicial e
a segunda valida dentro da função o JWT assinado por `HUB_JWT_SECRET`. `manifest_current` também é
publicada sem JWT de gateway porque serve leitura pública do manifesto já assinado; ela resolve o
ponteiro confirmado no banco, verifica o hash do objeto imutável e usa fallback last-good quando
necessário. O payload de outcomes exige `ts` alinhado à grade M1 (`ts % 60 == 0`); valores
desalinhados recebem `422`, nunca erro interno.

## Outcomes v2 e privacidade

As migrations `0010_outcomes_v2.sql` e `0011_outcome_budgets.sql` preservam a tabela histórica v1,
mas removem seu grant de insert anônimo direto. Toda escrita pública passa pela Edge Function e
por JWT assinado. O contrato v2 registra somente identidade pública da receita/engine/série e o
resultado terminal; conta da corretora, login, saldo, credencial e PII são proibidos.

Idempotência ocorre por evento e por cliente+sinal. A RPC atualiza o agregado na mesma transação;
clientes que observaram o mesmo sinal formam um grupo dependente e não multiplicam a amostra
estatística. Eventos normalizados têm retenção de 90 dias e agregados de 400 dias.

Limites: corpo de 256 KiB, 1–500 itens, sete dias, 60 requests/h por cliente, 600 requests/h
globais, 500 eventos/dia por cliente e 5.000 eventos/dia globais. `client_token` possui teto global
de 60 emissões/hora. As cotas globais continuam valendo quando alguém gira UUIDs.

Outcomes são telemetria não confiável para monitoramento. Nenhuma tabela, trigger ou função desse
fluxo promove estratégias ou altera manifestos/pesquisa.

## Arquivamento frio verificado

As migrations `0012`–`0014` substituem a conclusão antiga baseada somente em contagem. A Edge
Function `archive` apenas planeja jobs e mostra backlog; ela nunca gera Parquet nem apaga linhas.
O executor local usa:

```powershell
# Apenas planeja e informa o job; não autoriza remoção.
strategy-lab archive

# Executa upload privado, download, verificação e delete exato do recorte comprovado.
strategy-lab archive --execute --worker-id <identidade-operacional>
```

O modo `--execute` requer `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e o bucket privado `parquet`.
Também pode usar `SUPABASE_DB_URL` para as RPCs/tabelas; sem URL PostgreSQL, usa REST com a mesma
service role. Objetos são imutáveis e o reader hot+cold recusa hash, schema, contagem, PK ou
conteúdo divergente. O cron só planeja recortes de até 30 dias com ao menos 1.000 velas.

O backup padrão inclui o inventário e os objetos frios verificados. Use `--database-only` somente
quando a exclusão explícita de Storage fizer parte de outro plano de backup aprovado.

Nunca aplicar migrations com uma URL que contenha o ref de produção quando
`SUPABASE_PROD_REF` estiver definido.

## Secrets das functions

Configurar no Supabase, sem commitar valores:

```powershell
supabase secrets set HUB_ENV=staging
supabase secrets set MANIFEST_PUBKEY_A=<hex-public-key-a>
supabase secrets set MANIFEST_PUBKEY_B=<hex-public-key-b>
supabase secrets set HUB_JWT_SECRET=<random-hs256-secret>
supabase secrets set ARCHIVE_CONTROL_TOKEN=<random-control-secret>
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
supabase secrets set R2_ENDPOINT=<r2-endpoint>
supabase secrets set R2_BUCKET=<r2-bucket>
supabase secrets set R2_ACCESS_KEY_ID=<r2-access-key-id>
supabase secrets set R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
supabase secrets set R2_REGION=auto
```

`MANIFEST_TEST_PUBKEY` só pode ser configurada em staging (`HUB_ENV=staging`). Em produção, a
function rejeita a trust root de teste.

## Publicação recuperável

O fluxo CAT-13 usa uma saga explícita. `publish` grava `manifests/vN.json` sem overwrite, verifica
SHA-256, confirma o objeto no `publication_journal`, avança `manifest_pointers` apenas para versão
maior e cria uma entrada durável em `manifest_mirror_outbox`. Storage e Postgres não são tratados
como uma transação distribuída; reenvios do mesmo manifesto são idempotentes e falhas intermediárias
podem ser reparadas.

`current.json` continua existindo por compatibilidade, mas não é a autoridade. O canal preferido de
leitura é:

```powershell
curl -i "$env:SUPABASE_URL/functions/v1/manifest_current"
```

A resposta deve conter `ETag`, `Cache-Control`, `x-manifest-version` e `x-manifest-fallback`.

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

Resultado esperado no fluxo local/fixture:

- primeira submissão de versão nova: `201` com `sha256`;
- reenvio idempotente do mesmo manifesto: `200`;
- versão regressiva ou mesma versão com outro hash: `409`.

## Testes Deno

```powershell
cd apps/hub/supabase/functions
deno fmt --check
deno lint
deno check publish/index.ts outcomes/index.ts mirror/index.ts client_token/index.ts archive/index.ts tests/*.ts
deno test --allow-read=../../../../tests/fixtures,../../../../tests/keys
```
