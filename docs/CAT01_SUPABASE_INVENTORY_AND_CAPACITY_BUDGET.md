# CAT-01 — Inventário e orçamento do Supabase

Data da coleta: **2026-09-05 12:15:56 BRT / 15:15:56 UTC**
Commit inspecionado: `18d10bbf43615644a4ed57c50f9811403002135f`
Escopo: Strategy Lab/Hub, diagnóstico local e tentativa remota somente leitura
Resultado: **PARCIAL — inventário remoto BLOCKED por ausência de autenticação segura e staging confirmado**

## 1. Veredito executivo

O repositório contém uma CLI Supabase local funcional, seis migrations e cinco Edge Functions de
produto. Porém, esta sessão não possui `SUPABASE_ACCESS_TOKEN`, URL de banco de staging nem refs
de produção/staging. `supabase projects list --output json` falhou antes de acessar qualquer
projeto com `Access token not provided`. Portanto, plano, consumo, capacidade restante, objetos,
grants efetivos e estado das migrations **não foram medidos remotamente**.

Não foram reutilizadas credenciais anteriormente expostas em conversa. Elas não são um canal
seguro de automação e devem ser rotacionadas em tarefa de segurança separada. Não houve login,
DDL, DML, deploy, upload, download privado, exclusão ou acesso a conta de corretora.

O inventário estático encontrou riscos que impedem ativar retenção/limpeza hoje:

1. `archive` é um stub que sempre responde HTTP 501.
2. `complete_archive_job()` aceita somente uma contagem como prova antes de apagar candles; não
   verifica hash, schema, objeto restaurável ou cobertura por asset.
3. As funções `SECURITY DEFINER` não têm `REVOKE EXECUTE ... FROM PUBLIC` explícito nas migrations.
4. A publicação atual escreve `vN.json`, depois `current.json`, e só então a linha no banco, sem
   transação/compare-and-swap. Falha ou concorrência pode produzir estado parcial.
5. `client_token` assina por um ano qualquer UUID válido informado pelo chamador, sem prova de
   posse. Isso permite colisão/representação de um identificador conhecido e exige contrato de
   identidade ou aceitação explícita do risco antes de usar outcomes para governança.
6. O calendário inicial contém somente cinco pares spot e cinco OTC; não cobre todo o universo de
   16 símbolos hoje declarado pelo bot.
7. O índice de `payouts(asset, hour_ts)` repete a chave primária na mesma ordem. O índice desc de
   candles também pode ser redundante dependendo do workload. Nenhum deles pode ser removido sem
   estatísticas e `EXPLAIN` do ambiente real.

## 2. Ferramentas e conectividade

| Item | Evidência | Estado |
|---|---|---|
| Supabase CLI do PATH | não encontrada | indisponível |
| CLI pinada pelo Lab | `strategy-lab/state/tools/supabase-cli-v2.116.0/supabase.exe` | **2.116.0** |
| `supabase --help` | projetos, banco, functions, inspect e Storage listados | aprovado |
| Autenticação CLI | `SUPABASE_ACCESS_TOKEN` ausente; `projects list` recusado | **BLOCKED** |
| Banco staging | `SUPABASE_STAGING_DB_URL` ausente | **BLOCKED** |
| Ref produção | `SUPABASE_PROD_REF` ausente | **BLOCKED** |
| Ref staging | não existe valor verificável | **BLOCKED**; não inferida pelo nome |
| Config local | nenhum `supabase/config.toml` localizado | ausente |
| Deno | executável não encontrado | checks das functions não executados |
| Node | v22.23.2 | apenas inventariado |

O guard de staging em `strategy-lab/tests/conftest.py` pula testes quando a URL está ausente e só
recusa produção quando `SUPABASE_PROD_REF` foi fornecido. Assim, um futuro ensaio remoto exige os
dois valores seguros e uma confirmação independente do project ref; apenas o nome do projeto ou a
ausência do ref de produção não é prova de staging.

## 3. Inventário local da infraestrutura declarada

### 3.1 Migrations

| Migration | Conteúdo principal | Estado remoto |
|---|---|---|
| `0001_schema.sql` | nove tabelas e três índices explícitos | não verificado |
| `0002_rls.sql` | RLS/revokes e policy de insert de outcomes | não verificado |
| `0003_sessions_seed.sql` | sessões de 5 spot + 5 OTC | não verificado |
| `0004_archive_cron.sql` | pg_cron, pg_net, pgcrypto e arquivo após callback | não verificado |
| `0005_rate_limits.sql` | rate limit de outcomes | não verificado |
| `0006_holdout_burned.sql` | faixa de holdout consumida | não verificado |

Após as seis migrations, o schema pretendido tem onze tabelas:

- dados: `candles`, `payouts`, `market_sessions`, `gaps`;
- ciência/governança: `research_runs`, `holdout_burned`, `manifests`;
- operação: `collect_runs`, `archive_jobs`, `rate_limits`;
- cliente: `live_outcomes`.

RLS está declarado nas onze tabelas. A única policy declarada é insert anon de
`live_outcomes` para o próprio `client_id`. As Edge Functions, contudo, usam service role para
operações REST; por isso, grants, dono, `prosecdef`, `proconfig` e privilégios efetivos precisam
ser lidos diretamente do Postgres antes de afirmar isolamento.

### 3.2 Edge Functions e Storage pretendidos

| Function | Bytes autorais locais | Observação |
|---|---:|---|
| `publish` | 3.529 | valida/assina, mas publicação não é atômica |
| `outcomes` | 3.336 | lote até 500; limite 60 chamadas/h/cliente |
| `mirror` | 1.840 | replica somente `current.json` e `vN.json` |
| `client_token` | 1.341 | emite JWT de 1 ano para UUID informado pelo cliente |
| `archive` | 389 | **stub HTTP 501** |

Os buckets pretendidos são `manifests` público e `parquet` privado. A existência, políticas,
objetos, bytes, ETags, órfãos e espelho R2 não foram confirmados. O README manda executar
`supabase storage create`, mas a CLI 2.116.0 instalada oferece apenas `ls`, `cp`, `mv` e `rm` sob
`storage`; o procedimento de criação precisa ser corrigido em uma fase de implementação, não
improvisado contra produção.

Arquivos públicos locais medidos:

| Arquivo | Bytes |
|---|---:|
| `data/manifest.json` | 16.763 |
| `cache/manifest.json` | 16.763 |
| fixture de manifesto do Lab | 1.405 |
| vetores de aceitação do manifesto | 114.639 |

`strategy-lab/state` soma 371.308.727 bytes em 7.533 arquivos, mas isso é majoritariamente
ferramenta/ambiente local e **não mede Storage nem banco Supabase**. Não é candidato de limpeza
desta fase. `strategy-lab/research/runs` contém 6 arquivos/33.563 bytes; não há diretório local de
backups.

## 4. Medidas remotas não executadas

Os itens abaixo permanecem `BLOCKED`, e não devem ser convertidos em zero:

- plano contratado, quota útil e capacidade restante;
- tamanho total do banco, WAL, TOAST, índices, bloat e tuplas mortas;
- linhas por tabela, ativo e período, médias e p95 reais por linha/lote;
- extensões disponíveis/instaladas e jobs `pg_cron` ativos;
- migrations realmente aplicadas e divergência de schema;
- Edge Functions implantadas, versões, invocações, latência e erros;
- buckets, políticas, objetos, órfãos e bytes de Storage/R2;
- egress normal/cached, conexões, CPU, RAM e limites do projeto;
- policies, grants e privilégios `EXECUTE` efetivos.

Consequentemente, **capacidade restante real = desconhecida**. Os números da seção seguinte são
cenários de engenharia, não medição do projeto do operador.

## 5. Modelo de crescimento

### 5.1 Candles

Limite superior 24×7 para M1:

`rows = assets × 1.440 × days`

Como bytes/linha reais não puderam ser medidos, foi usada uma faixa provisória de 200–400 bytes por
linha, incluindo índice/TOAST, com ponto central de 300 bytes. Essa hipótese deve ser substituída
por `pg_total_relation_size(candles) / reltuples` e p95 de lotes quando o staging estiver acessível.

| Ativos | Dias | Linhas (24×7) | Central 300 B | Faixa 200–400 B |
|---:|---:|---:|---:|---:|
| 16 | 7 | 161.280 | 48,384 MB | 32,256–64,512 MB |
| 16 | 30 | 691.200 | 207,360 MB | 138,240–276,480 MB |
| 16 | 180 | 4.147.200 | 1,244 GB | 0,829–1,659 GB |
| 30 | 7 | 302.400 | 90,720 MB | 60,480–120,960 MB |
| 30 | 30 | 1.296.000 | 388,800 MB | 259,200–518,400 MB |
| 30 | 180 | 7.776.000 | 2,333 GB | 1,555–3,110 GB |

O calendário pretendido reduz isso: um spot sem feriados soma 7.020 minutos/semana e um OTC de
fim de semana, 2.880. Um universo ilustrativo de 8 spot + 8 OTC produziria 79.200 candles/semana,
49,1% do teto 24×7. Porém, o seed atual não contém todos esses 16 símbolos; não se deve usar essa
redução para reservar capacidade até corrigir e medir as sessões reais.

`payouts` cresce no máximo `assets × 24 × days`: 11.520 linhas/mês para 16 ativos e 21.600 para
30, antes de considerar sessões. O volume é pequeno frente a candles, mas amostras zero e retenção
precisam continuar explícitas.

### 5.2 Clientes, Hub e manifesto

Hipótese de dimensionamento: check condicional a cada 15 minutos (96/dia), 30 dias e manifesto de
30 KB. O manifesto atual tem 16,763 KB; 30 KB reserva crescimento.

| Clientes | Checks/mês | Se baixar 30 KB sempre | Se baixar 30 KB 1×/dia |
|---:|---:|---:|---:|
| 100 | 288.000 | 8,64 GB | 0,09 GB |
| 1.000 | 2.880.000 | 86,40 GB | 0,90 GB |

ETag/304 e CDN tornam o segundo perfil plausível, mas headers e cache miss também consomem
tráfego. O bot deve continuar executando localmente; clientes não consultam candles nem calculam
pesquisa no Hub.

Para `outcomes`, o limite atual permite 60 invocações/hora/cliente. Um comportamento de 12 chamadas
por hora (a cada 5 min), mesmo vazio, resultaria em 864.000 invocações/mês para 100 clientes e
8.640.000 para 1.000. Isso excede os 500.000 incluídos no Free e os 2 milhões incluídos no Pro.
O cliente deve enviar somente lotes não vazios e agregar com segurança; frequência final precisa
ser medida. Hoje a função aceita lote vazio e ainda consome rate limit, um desperdício a corrigir
de forma testada.

### 5.3 Limites públicos usados apenas como referência

Em 2026-09-05, a página pública do Supabase informa:

- Free: 500 MB de banco, 1 GB de Storage, 5 GB de egress + 5 GB cached e 500 mil invocações de
  Edge Functions/mês;
- Pro: 8 GB de disco por projeto, 100 GB de Storage, 250 GB de egress + 250 GB cached e 2 milhões
  de invocações/mês incluídas;
- Edge Functions: 256 MB, 2 s de CPU por request, duração máxima de 150 s no Free e 400 s nos
  planos pagos.

Fontes: [preços e quotas](https://supabase.com/pricing),
[limites de Edge Functions](https://supabase.com/docs/guides/functions/limits),
[tamanho de banco/disco](https://supabase.com/docs/guides/platform/database-size) e
[remoção de objetos](https://supabase.com/docs/guides/storage/management/delete-objects).

Esses limites mudam e **não identificam o plano atual**. Banco lógico e disco total também são
medidas diferentes: WAL e arquivos internos usam disco além das relações. A documentação informa
que um projeto novo já ocupa aproximadamente 40–60 MB.

## 6. Budgets propostos

Os thresholds seguintes são nomes/percentuais iniciais. O valor absoluto aplicável será calculado
somente depois de confirmar a quota do projeto.

| Budget | Warning | Critical | Ação permitida |
|---|---:|---:|---|
| `hub_database_occupancy` | 70% | 85% | parar expansão/backfill; nunca reconciliação |
| `hub_storage_occupancy` | 70% | 85% | pausar arquivo novo se não houver espaço verificável |
| `hub_monthly_egress` | 70% | 85% | aumentar cache/condicional; preservar manifesto last-good |
| `hub_edge_invocations` | 70% | 85% | agregar requests não vazios; não perder outcomes |
| `hub_realtime_messages` | 70% | 85% | não usar Realtime sem necessidade demonstrada |
| `hub_function_error_rate` | 1% | 5% | bloquear publicação; manter versão anterior íntegra |
| `hub_archive_backlog` | 7 dias | 14 dias | pausar ingestão expansiva; sem DELETE automático |
| `hub_manifest_size` | 512 KiB | 1 MiB | particionar distribuição após contrato compatível |
| `lab_concurrent_research` | 1 job | 2 jobs | limitar localmente até medir CPU/IO/banco |

Valores ilustrativos a 70%/85%, não aplicados:

| Recurso | Free warning/critical | Pro warning/critical |
|---|---:|---:|
| banco/disco incluído | 350/425 MB | 5,6/6,8 GB |
| Storage | 0,70/0,85 GB | 70/85 GB |
| egress | 3,5/4,25 GB | 175/212,5 GB |
| Edge invocations | 350.000/425.000 | 1.400.000/1.700.000 |

Para Free, 30 ativos×30 dias no teto 24×7 já estimam 259–518 MB apenas em candles; portanto não
há base para prometer 30 dias quentes. O alvo provisório deve ser **7 dias mínimos e até 30 dias
somente quando a medida real permanecer abaixo de warning**, preservando o dataset selado em
arquivo frio restaurável. O arquivo frio não está pronto hoje; logo, nenhuma exclusão é autorizada.

## 7. Índices a verificar, não remover

1. `payouts_asset_hour_ts_idx`: mesmas colunas e ordem da PK; candidato forte a duplicação.
2. `candles_asset_ts_desc_idx`: uma btree da PK pode atender scans reversos, mas collation, filtros,
   estatística e consulta real precisam ser conferidos.
3. `live_outcomes_strategy_key_ts_idx`: não é coberto pela PK iniciada por `client_id`; parece
   necessário para agregação por estratégia.

Prova futura mínima: `pg_stat_user_indexes`, tamanho, scans desde reset conhecido, `EXPLAIN
(ANALYZE, BUFFERS)` no staging com distribuição representativa e benchmark antes/depois. Remoção
exige migration nova e reversível; migrations 0001–0006 permanecem imutáveis.

## 8. Pacote de consultas somente leitura para a retomada

Executar somente após autenticação por perfil seguro e confirmação independente do ref de staging:

```sql
select current_database(), current_user, version();
select extname, extversion from pg_extension order by extname;
select * from supabase_migrations.schema_migrations order by version;
select schemaname, relname, n_live_tup, n_dead_tup
from pg_stat_user_tables order by schemaname, relname;
select n.nspname, c.relname, pg_total_relation_size(c.oid) as total_bytes,
       pg_relation_size(c.oid) as heap_bytes,
       pg_indexes_size(c.oid) as index_bytes
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r','p') order by total_bytes desc;
select asset, min(ts), max(ts), count(*) from public.candles group by asset order by asset;
select asset, count(*), avg(samples), percentile_cont(0.95) within group (order by samples)
from public.payouts group by asset order by asset;
select schemaname, tablename, policyname, roles, cmd, qual, with_check
from pg_policies order by schemaname, tablename, policyname;
select routine_name, security_type from information_schema.routines
where routine_schema = 'public' order by routine_name;
select grantee, routine_name, privilege_type
from information_schema.routine_privileges
where routine_schema = 'public' order by routine_name, grantee;
select jobid, jobname, schedule, active from cron.job order by jobid;
select * from pg_stat_user_indexes order by relname, indexrelname;
```

Para objetos e quotas, usar listagem/relatórios da API/CLI autenticada sem baixar conteúdo privado.
Comparar inventário de `storage.objects` com listagem da Storage API. Qualquer remoção futura deve
usar a Storage API; apagar só metadados SQL cria órfãos. `DELETE` não reduz necessariamente o
espaço físico imediatamente, e `VACUUM FULL` bloqueia a tabela; ambos estão fora deste CAT.

Comandos seguros usados/esperados:

```powershell
.\strategy-lab\state\tools\supabase-cli-v2.116.0\supabase.exe --version
.\strategy-lab\state\tools\supabase-cli-v2.116.0\supabase.exe --help
.\strategy-lab\state\tools\supabase-cli-v2.116.0\supabase.exe projects list --output json
.\strategy-lab\state\tools\supabase-cli-v2.116.0\supabase.exe inspect db --help
.\strategy-lab\state\tools\supabase-cli-v2.116.0\supabase.exe storage --help
```

Não incluir tokens, senhas, URLs com credencial ou `--debug` em logs compartilhados.

## 9. Retenção e próximos gates

- **Agora:** zero remoção; coleta expansiva e cron de arquivo não devem ser ativados.
- **Alternativa sem pg_net/pg_cron:** job local/VPS `collect --archive` somente após CAT-17 provar
  objeto, hash, contagem, schema e restore. Ausência remota das extensões ainda não foi comprovada.
- **CAT-02:** pode avançar localmente com o contrato público, respeitando os bloqueios do CAT-00.
- **CAT-13/CAT-14:** devem tornar publicação/consumo transacionais ou recuperáveis e observáveis.
- **CAT-17:** implementa arquivo frio e restore verificado.
- **CAT-18:** só então executa limpeza por plano fechado, dry-run e targets explícitos.

## 10. Critérios de aceite do CAT-01

| Critério | Resultado |
|---|---|
| relatório com data/hora, fórmulas e capacidade | **PARCIAL**: fórmulas e referências prontas; capacidade real BLOCKED |
| comandos reproduzíveis sem segredos | **PASS** |
| plano de retenção preserva datasets | **PASS como proposta; aplicação BLOCKED por falta de arquivo/restore** |
| refs/plano/quota/objetos remotos confirmados | **BLOCKED**: sem autenticação segura/staging |
| zero DDL/DELETE remoto | **PASS** |

O CAT-01 não certifica o Supabase de produção nem autoriza usar os números estimados como consumo
real. A retomada remota precisa começar pela rotação das credenciais já expostas, configuração de
um perfil seguro, confirmação explícita de staging e repetição deste inventário somente leitura.
