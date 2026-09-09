# CAT-18 — Retenção, quota e limpeza fechada

## Escopo

O comando `strategy-lab retention` reconcilia um inventário do projeto e gera um plano
determinístico. O padrão é sempre `dry-run`; inventário, bytes, idade, proprietário, referências,
hash e provas de backup/restore aparecem no relatório. Objetos ligados a manifests, runs,
holdouts, datasets ou evidências financeiras são protegidos.

## Segurança operacional

Cada plano contém `project_ref`, ambiente, hash do snapshot e alvos exatos com tamanho, versão e
SHA-256. A aplicação exige o token `RETENTION-APPLY:<plan_hash>`, revalida o snapshot e aborta se
qualquer item mudou. Não há wildcard, cascade ou drop. Produção exige `--allow-production` além da
confirmação explícita; nenhum alvo de produção foi executado nesta CAT.

```powershell
strategy-lab retention --dry-run --project-ref jciclczthkbpvvqnrnbf --environment staging `
  --output retention-plan.json
strategy-lab retention --execute --plan-file retention-plan.json `
  --confirm RETENTION-APPLY:<hash>
```

O adaptador REST usa apenas a service role no processo local e só expõe remoção de caminho exato.
Sem credenciais, o CLI usa um repositório vazio para permitir auditoria offline sem rede.

## Política e quota

Temporários expiram em 7 dias; staging expirado em 30 dias; logs arquivados em 90 dias; publicações
órfãs em 14 dias. Logs/publicações exigem backup e restore verificados; velas frias exigem arquivo
verificado. Quota separa bytes físicos de objetos, linhas, backlog e dados logicamente reutilizáveis.
Em `warning` o relatório pede revisão; em `critical` recomenda pausar ingestão/pesquisa nova e
executar um plano dry-run. Evidência nunca é apagada para liberar espaço.

## Persistência

A migration `0015_retention_plans.sql` registra plano, alvos e eventos append-only com RLS sem
acesso público. A execução remota em lote deve registrar o plano e aplicar somente depois da
revalidação do snapshot; benchmarks/EXPLAIN de índices permanecem fora desta CAT.

## Limitações desta validação

O bucket remoto `manifests` foi inventariado somente em modo leitura e estava vazio durante a
validação. Não houve limpeza remota, alteração de dados, acesso a corretora ou ambiente Real. A
política de archive frio continua fail-closed até que as tabelas de prova confirmem backup e restore.
