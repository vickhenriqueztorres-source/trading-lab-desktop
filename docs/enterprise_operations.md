# Enterprise operacional — Fase 3

Esta fase adiciona controles operacionais sem habilitar execução financeira nova. O Core continua
único escritor financeiro, a Deriv Real continua somente leitura e qualquer teste de corretora usa
workers simulados ou Demo explicitamente autorizada.

## Componentes

- `apps/core/observability/slo.py`: SLOs, burn rate, budget e severidade.
- `apps/core/security/audit_log.py`: eventos append-only com cadeia SHA-256 e assinatura HMAC.
- `packages/persistence/migrations.py`: API expand/migrate/contract compatível com as migrações
  SQLite publicadas; não se altera migração já aplicada.
- `apps/core/resilience/chaos.py`: injeção limitada e callback-only para testes locais.
- `deploy/`, `operations/` e `docs/runbooks/`: operação reversível, backup/restore e resposta.

O layout histórico usa `packages/persistence/migrations.py` (módulo único). A API
`MigrationPhase`/`SchemaMigrator` foi incorporada nele para evitar dois pacotes com o mesmo nome e
preservar imports públicos existentes.

## Limites

Os scripts de deploy e backup são templates operacionais: exigem variáveis explícitas, checksum e
perfil isolado. O canary só pode trafegar sinais sombra/read-only. Rollback preserva bancos, vaults,
logs e ordens ambíguas.
