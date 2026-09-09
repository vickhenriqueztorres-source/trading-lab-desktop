# CAT-17 — Arquivamento frio verificável e restaurável

Data: 2026-09-06 BRT
Escopo: Strategy Lab e Hub Supabase staging
Requisitos: R-HUB-7, R-OPS-1, continuidade de R-COL-3/R-COL-6/R-RES-1
Invariantes preservadas: I-1, I-2, I-6, I-7, I-8 e I-14

## Resultado

O stub de archive foi substituído por um protocolo em duas partes. A Edge Function apenas cria
jobs e informa backlog; a conversão Parquet ocorre no processo isolado do Strategy Lab. Nenhum
upload, resposta HTTP ou contagem declarada autoriza remoção sozinho.

A migration antiga `complete_archive_job(uuid,bigint)` foi desabilitada e perdeu execução pública.
O caminho novo só remove PKs congeladas quando todos os valores OHLC, volume, origem e instante de
coleta ainda são idênticos. Uma atualização tardia retorna zero, marca falha e preserva a camada
quente.

## Fluxo comprovado

1. O cron planeja no máximo uma partição de 30 dias por execução e ativo.
2. Partições abaixo de 1.000 velas permanecem quentes, evitando microarquivos diários.
3. O job congela as PKs, conteúdo e observações pontuais de payout.
4. Um executor obtém claim com token e lease.
5. O Lab gera Parquet Zstandard com `decimal128(18,8)` para OHLC e `decimal128(5,2)` para payout.
6. O upload usa caminho derivado de ativo, intervalo e fingerprint e não permite overwrite.
7. O próprio processo baixa o objeto e verifica SHA-256 dos bytes, versão de schema, contagem,
   unicidade das PKs e fingerprint lógico integral.
8. A prova é persistida e somente então a RPC exclui as linhas exatas ainda idênticas.
9. O reader de pesquisa consulta apenas objetos concluídos e repete todas as verificações antes de
   unir hot+cold. PK igual com conteúdo diferente falha fechado.
10. `collection_watermarks` permanece separado de `MAX(candles.ts)`, portanto arquivar não provoca
    novo download de toda a história.

## Backup e restore

O backup padrão passa a inventariar também os objetos frios verificados, além do `pg_dump`.
Cada objeto é baixado, verificado e salvo pelo SHA-256; o inventário recebe checksum próprio.
O restore escreve em destino isolado, baixa novamente e confirma hash, conteúdo e contagem.
`--database-only` existe somente como exclusão explícita dos objetos, não como padrão silencioso.

## Evidência staging

Inventário anterior à migration: `candles=0`, `old_candles=0`, `legacy_jobs=0`,
`parquet_objects=0`. As migrations 0012–0014 foram aplicadas no projeto staging vinculado e o
`db lint --linked --level error` terminou sem erros.

O primeiro smoke falhou fechado e revelou a regex de caminho superescapada na migration 0012.
Nenhuma vela foi removida. A correção foi registrada, sem reescrever migration aplicada, em 0013.

Round-trip posterior, com fixture exclusivo `CAT17TEST`:

| Prova | Resultado |
| --- | ---: |
| velas congeladas | 3 |
| objeto baixado e verificado | 1 |
| bytes Parquet | 4.341 |
| fingerprint lógico | `04db071ac8f4755f35b98e950c814bc35063ac9967901fcf5fa36edce658ed9a` |
| linhas removidas da camada quente | 3 |
| velas reconstruídas pelo reader research | 3 |
| payouts point-in-time disponíveis | 3/3 |
| objetos restaurados em destino isolado | 1/1 |

A razão bytes Parquet/JSON lógico foi `5,161712` nesse fixture artificial de apenas três linhas;
isso não representa compressão operacional. A migration 0014 impede essas partições pequenas no
cron real com piso de 1.000 linhas. Medição representativa de volume fica para carga real.

Após o smoke, somente o fixture, seus jobs e seu objeto foram removidos. Verificação final:
`candles=0`, `payouts=0`, `jobs=0`, `objects=0` para `CAT17TEST`.

## Falhas testadas

- crash depois do upload e retomada idempotente sem segundo objeto;
- download corrompido;
- quota de Storage;
- mesma contagem com conteúdo alterado;
- correção tardia antes do delete;
- PK duplicada;
- executor ausente;
- objeto alterado depois de indexado;
- merge hot+cold sem duplicação;
- watermark após remoção da camada quente;
- backup e restore em destino isolado.

## Validação

- CAT-17 focado: 14 testes aprovados;
- Strategy Lab completo: 396 aprovados, 3 staging ignorados por ausência da URL PostgreSQL no
  processo do pytest;
- Ruff check e format: aprovados;
- mypy strict: aprovado em 88 fontes;
- compileall: aprovado;
- Supabase migration dry-run/push, db lint, deploy da função e smoke HTTP: aprovados;
- `git diff --check`: aprovado.

Os três testes staging históricos continuam pulados porque exigem `SUPABASE_STAGING_DB_URL`; esta
CAT executou o round-trip remoto por API/CLI com credenciais efêmeras em memória. Nenhuma corretora,
conta financeira, ordem, ambiente Real ou dado de cliente foi acessado.
