# Documentação final da plataforma

## Arquitetura

Launcher/supervisor inicia UI, Auth Agent, Trading Core, Deriv Worker e IQ Option Worker isolados. O Core é o único escritor financeiro; workers traduzem protocolos; UI é projeção.

## Componentes implementados

Domínio de ordens, state store, worker resiliente, circuit breakers, reconciliação, fila/single writer, PostgreSQL/Redis opcionais, lease com fencing, supervisor, SLO, auditoria HMAC, migrações por fases, caos local, deploy/rollback, backup/restore e runbooks.

## SLO/SLI

Disponibilidade de trading-ready, latência de ACK p95/p99, reconciliação p95/p99, submissão duplicada e UNKNOWN não resolvido. Métricas não contêm credenciais.

## Operação

Use docs/deploy_procedure.md, docs/disaster_recovery.md, docs/slo_dashboard.md e docs/runbooks/. O bot inicia desarmado e reconexão não rearma.

## Emergência

Contatos devem ser preenchidos pelo operador no ambiente de implantação; nenhum contato ou segredo é embutido no código.

## Próximos passos

Executar scanners no CI, restore drill contra infraestrutura aprovada, soak Demo externo opt-in e obter assinaturas operacionais. Merge/tag só após os gates correspondentes.

