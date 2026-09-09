# CAT-13 — Publicacao recuperavel do manifesto

Data: 2026-09-06.

Escopo: Strategy Lab Hub Supabase, Edge Functions `publish`, `mirror` e `manifest_current`.
Nao envolve corretora, credencial financeira, envio de ordem ou alteracao no EXE principal.

## Objetivo

Publicar manifestos aprovados sem depender de `current.json` como fonte de verdade e sem tratar
Storage + Postgres como uma transacao distribuida. O contrato autoritativo passa a ser:

1. bytes canonicos assinados;
2. objeto imutavel `manifests/vN.json`;
3. linha de journal da publicacao;
4. ponteiro confirmado em banco;
5. projecao legada `current.json`, reparavel.

## Fluxo

1. `publish` valida JSON, schema, assinatura e politica de producao.
2. `reserve_manifest_publication(...)` serializa por canal com advisory lock e cria o journal.
3. `publish` grava `vN.json` sem overwrite (`x-upsert=false`) e verifica o hash armazenado.
4. `mark_manifest_object_stored(...)` registra que o objeto imutavel foi confirmado.
5. `commit_manifest_publication(...)` insere `manifests`, avanca `manifest_pointers` somente se
   `N` for maior que o ponteiro atual e cria job duravel em `manifest_mirror_outbox`.
6. `publish` repara `current.json` a partir do ponteiro confirmado e registra o estado no journal.
7. `mirror` consome a outbox, copia para R2 e verifica hash de origem e destino.

## Recuperacao

| Falha | Resultado esperado | Recuperacao |
|---|---|---|
| Banco indisponivel antes da reserva | `503`, nenhum objeto gravado | Repetir depois |
| Upload falha apos reserva | Journal fica reservado com falha | Reenvio idempotente grava o mesmo `vN` |
| Objeto existe com hash errado | `409`, fail-closed | Revisao manual; nao sobrescreve `vN` |
| Commit falha apos objeto confirmado | `503`, objeto imutavel preservado | Reenvio verifica hash e conclui commit |
| `current.json` falha | Publicacao continua autoritativa | Reenvio ou reparo por `manifest_current` |
| Mirror falha | Outbox registra tentativa e backoff | `mirror` retenta ate limite configurado |

## Producao

Publicacao em `production` exige evidencia real selada:

- `schema_revision == "1.2"`;
- `dataset_evidence.kind == "real_market"`;
- `research_run` finalizado com sucesso;
- snapshot de dataset elegivel;
- tentativa aprovada;
- artefato de selecao de portfolio.

Chave de teste so e aceita em staging. Versao regressiva ou mesma versao com hash diferente retorna
`409`.

## Consumo

O endpoint `manifest_current` resolve o ponteiro confirmado em banco e baixa o objeto imutavel. Se
o objeto apontado estiver indisponivel ou corrompido, ele tenta o ultimo objeto bom comprometido e
marca `x-manifest-fallback: true`. `current.json` permanece apenas como canal legado e reparavel.

## Validacao local

- `deno check`: aprovado.
- `deno test`: 20 testes aprovados.
- `pytest tests/test_cat13_publication_pipeline.py`: 6 testes aprovados.

Os testes cobrem chaves A/B, rejeicao de chave de teste em producao, versao regressiva, idempotencia,
interrupcoes em Storage/DB, hash errado, reparo de `current.json`, fallback last-good, mirror outbox
e contrato SQL/deploy.
